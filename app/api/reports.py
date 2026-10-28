
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.dependencies import require_roles
from app.models.purchase_order import PurchaseOrder
from app.models.supplier_invoice import SupplierInvoice
from app.models.user import User
from app.services.reports import BUILDERS, REPORT_ACCESS, ReportFilters, build_report

router = APIRouter(
    prefix="/reports",
    tags=["Reports and Analytics APIs"],
)

REPORT_ROLES = (
    "SUPER_ADMIN",
    "ADMIN",
    "FINANCE_MANAGER",
    "INVENTORY_MANAGER",
    "ORDER_MANAGER",
)


ANALYTICS_ROLES = REPORT_ROLES + ("PRODUCT_MANAGER",)


def validate_date_range(
    date_from: date | None,
    date_to: date | None,
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=400,
            detail="date_from must be on or before date_to.",
        )


@router.get("/purchase-orders")
def purchase_order_report(
    date_from: date | None = None,
    date_to: date | None = None,
    status_filter: str | None = Query(
        default=None, alias="status"
    ),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*REPORT_ROLES)),
):
    validate_date_range(date_from, date_to)

    query = db.query(PurchaseOrder)

    if date_from:
        query = query.filter(
            PurchaseOrder.created_at >= datetime.combine(
                date_from, time.min
            )
        )

    if date_to:
        next_day = date_to + timedelta(days=1)
        query = query.filter(
            PurchaseOrder.created_at
            < datetime.combine(next_day, time.min)
        )

    if status_filter:
        query = query.filter(
            PurchaseOrder.status == status_filter.upper()
        )

    rows = query.order_by(
        PurchaseOrder.created_at.desc()
    ).all()

    totals_by_currency: dict[str, Decimal] = {}

    for row in rows:
        totals_by_currency[row.currency] = (
            totals_by_currency.get(row.currency, Decimal("0.00"))
            + Decimal(str(row.total_amount))
        )

    return {
        "report_type": "purchase_orders",
        "row_count": len(rows),
        "totals_by_currency": {
            currency: str(total)
            for currency, total in totals_by_currency.items()
        },
        "rows": [
            {
                "id": row.id,
                "po_number": row.po_number,
                "supplier_id": row.supplier_id,
                "status": row.status,
                "currency": row.currency,
                "total_amount": str(row.total_amount),
                "created_at": (
                    row.created_at.isoformat()
                    if row.created_at
                    else None
                ),
            }
            for row in rows
        ],
    }


@router.get("/supplier-invoices")
def supplier_invoice_report(
    date_from: date | None = None,
    date_to: date | None = None,
    status_filter: str | None = Query(
        default=None, alias="status"
    ),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*REPORT_ROLES)),
):
    validate_date_range(date_from, date_to)

    query = db.query(SupplierInvoice)

    if date_from:
        query = query.filter(
            SupplierInvoice.invoice_date >= date_from
        )
    if date_to:
        query = query.filter(
            SupplierInvoice.invoice_date <= date_to
        )
    if status_filter:
        query = query.filter(
            SupplierInvoice.status == status_filter.upper()
        )

    rows = query.order_by(
        SupplierInvoice.invoice_date.desc()
    ).all()

    unpaid_totals_by_currency: dict[str, Decimal] = {}

    for row in rows:
        if row.status != "PAID" and row.status != "VOID":
            unpaid_totals_by_currency[row.currency] = (
                unpaid_totals_by_currency.get(
                    row.currency, Decimal("0.00")
                )
                + Decimal(str(row.total_amount))
            )

    return {
        "report_type": "supplier_invoices",
        "row_count": len(rows),
        "unpaid_totals_by_currency": {
            currency: str(total)
            for currency, total in unpaid_totals_by_currency.items()
        },
        "rows": [
            {
                "id": row.id,
                "invoice_number": row.invoice_number,
                "supplier_id": row.supplier_id,
                "purchase_order_id": row.purchase_order_id,
                "status": row.status,
                "invoice_date": row.invoice_date.isoformat(),
                "due_date": (
                    row.due_date.isoformat()
                    if row.due_date
                    else None
                ),
                "currency": row.currency,
                "total_amount": str(row.total_amount),
            }
            for row in rows
        ],
    }


@router.get("/catalog")
def report_catalog(current_user: User = Depends(require_roles(*ANALYTICS_ROLES))):
    """Reports the current user may run."""
    return {
        "reports": sorted(
            name for name, roles in REPORT_ACCESS.items()
            if current_user.role.value in roles
        ),
        "filters": ["date_from", "date_to", "status", "category_id", "warehouse_id"],
        "export_formats": ["csv", "xlsx", "pdf"],
    }


@router.get("/summary")
def report_summary(
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*REPORT_ROLES)),
):
    purchase_order_count = db.query(PurchaseOrder).count()
    invoice_count = db.query(SupplierInvoice).count()

    purchase_order_totals = (
        db.query(
            PurchaseOrder.currency,
            __import__("sqlalchemy").func.sum(
                PurchaseOrder.total_amount
            ),
        )
        .group_by(PurchaseOrder.currency)
        .all()
    )

    invoice_totals = (
        db.query(
            SupplierInvoice.currency,
            __import__("sqlalchemy").func.sum(
                SupplierInvoice.total_amount
            ),
        )
        .group_by(SupplierInvoice.currency)
        .all()
    )

    return {
        "purchase_order_count": purchase_order_count,
        "supplier_invoice_count": invoice_count,
        "purchase_order_totals_by_currency": {
            currency: str(total or 0)
            for currency, total in purchase_order_totals
        },
        "supplier_invoice_totals_by_currency": {
            currency: str(total or 0)
            for currency, total in invoice_totals
        },
    }




@router.get("/{report_name}")
def run_report(
    report_name: str,
    date_from: date | None = None,
    date_to: date | None = None,
    status_filter: str | None = Query(default=None, alias="status", max_length=30),
    category_id: int | None = Query(default=None, gt=0),
    warehouse_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*ANALYTICS_ROLES)),
):
    """Sales, orders, inventory, payments, refunds, returns and product /
    category performance reports with date, status, category and warehouse
    filters. See GET /reports/catalog for the list."""
    if report_name not in BUILDERS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown report. Available: {', '.join(sorted(BUILDERS))}",
        )
    if current_user.role.value not in REPORT_ACCESS[report_name]:
        raise HTTPException(status_code=403, detail="You may not access this report")
    validate_date_range(date_from, date_to)

    result = build_report(
        db,
        report_name,
        ReportFilters(date_from, date_to, status_filter, category_id, warehouse_id),
    )
    return {
        "report_type": result["report"],
        "row_count": len(result["rows"]),
        "columns": result["columns"],
        "summary": result["summary"],
        "rows": result["rows"],
    }
