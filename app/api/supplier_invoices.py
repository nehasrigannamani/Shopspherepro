
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.dependencies import require_roles
from app.models.supplier import Supplier
from app.models.purchase_order import PurchaseOrder
from app.models.supplier_invoice import SupplierInvoice
from app.schemas.supplier_invoices import (
    SupplierInvoiceCreate,
    SupplierInvoiceStatusUpdate,
    SupplierInvoiceResponse,
)

router = APIRouter(
    prefix="/supplier-invoices",
    tags=["Supplier Invoice Management"],
)

STAFF_ROLES = (
    "SUPER_ADMIN",
    "ADMIN",
    "FINANCE_MANAGER",
    "INVENTORY_MANAGER",
)


@router.post(
    "",
    response_model=SupplierInvoiceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_supplier_invoice(
    payload: SupplierInvoiceCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*STAFF_ROLES)),
):
    supplier = (
        db.query(Supplier)
        .filter(Supplier.id == payload.supplier_id)
        .first()
    )
    if supplier is None:
        raise HTTPException(404, "Supplier not found.")

    if not supplier.is_active:
        raise HTTPException(
            400, "Cannot invoice an inactive supplier."
        )

    if payload.purchase_order_id is not None:
        po = (
            db.query(PurchaseOrder)
            .filter(PurchaseOrder.id == payload.purchase_order_id)
            .first()
        )
        if po is None:
            raise HTTPException(404, "Purchase order not found.")
        if po.supplier_id != payload.supplier_id:
            raise HTTPException(
                400,
                "Purchase order belongs to a different supplier.",
            )
        if po.status == "CANCELLED":
            raise HTTPException(
                400, "Cannot invoice a cancelled purchase order."
            )

    subtotal = Decimal(str(payload.subtotal))
    tax = Decimal(str(payload.tax_amount))

    invoice = SupplierInvoice(
        invoice_number=payload.invoice_number.strip(),
        supplier_invoice_number=payload.supplier_invoice_number,
        supplier_id=payload.supplier_id,
        purchase_order_id=payload.purchase_order_id,
        invoice_date=payload.invoice_date,
        due_date=payload.due_date,
        currency=payload.currency.upper(),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        status="RECORDED",
        notes=payload.notes,
        created_by=current_user.id,
    )

    try:
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409,
            "Invoice number already exists or a data constraint was violated.",
        )

    return invoice


@router.get("", response_model=list[SupplierInvoiceResponse])
def list_supplier_invoices(
    status_filter: str | None = Query(default=None, alias="status"),
    supplier_id: int | None = Query(default=None, gt=0),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*STAFF_ROLES)),
):
    query = db.query(SupplierInvoice)

    if status_filter:
        query = query.filter(
            SupplierInvoice.status == status_filter.upper()
        )
    if supplier_id is not None:
        query = query.filter(
            SupplierInvoice.supplier_id == supplier_id
        )

    return (
        query.order_by(SupplierInvoice.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/{invoice_id}", response_model=SupplierInvoiceResponse)
def get_supplier_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*STAFF_ROLES)),
):
    invoice = (
        db.query(SupplierInvoice)
        .filter(SupplierInvoice.id == invoice_id)
        .first()
    )
    if invoice is None:
        raise HTTPException(404, "Supplier invoice not found.")
    return invoice


@router.patch(
    "/{invoice_id}/status",
    response_model=SupplierInvoiceResponse,
)
def update_supplier_invoice_status(
    invoice_id: int,
    payload: SupplierInvoiceStatusUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*STAFF_ROLES)),
):
    invoice = (
        db.query(SupplierInvoice)
        .filter(SupplierInvoice.id == invoice_id)
        .with_for_update()
        .first()
    )
    if invoice is None:
        raise HTTPException(404, "Supplier invoice not found.")

    transitions = {
        "RECORDED": {"APPROVED", "VOID"},
        "APPROVED": {"PAID", "VOID"},
        "PAID": set(),
        "VOID": set(),
    }

    if payload.status not in transitions.get(invoice.status, set()):
        raise HTTPException(
            400,
            f"Invalid invoice transition: {invoice.status} "
            f"to {payload.status}.",
        )

    invoice.status = payload.status
    if payload.status == "PAID":
        invoice.paid_at = datetime.utcnow()

    db.commit()
    db.refresh(invoice)
    return invoice
