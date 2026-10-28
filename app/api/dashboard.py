
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.dependencies import require_roles
from app.models.inventory import Inventory
from app.models.order import Order
from app.models.payment import Payment
from app.models.refund import Refund
from app.models.return_request import ReturnRequest
from app.models.user import User
from app.models.product import Product
from app.models.supplier import Supplier
from app.models.purchase_order import PurchaseOrder
from app.models.supplier_invoice import SupplierInvoice
from app.schemas.dashboard import DashboardSummary

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard and Summary APIs"],
)

DASHBOARD_ROLES = (
    "SUPER_ADMIN",
    "ADMIN",
    "PRODUCT_MANAGER",
    "INVENTORY_MANAGER",
    "ORDER_MANAGER",
    "FINANCE_MANAGER",
)


@router.get("/summary", response_model=DashboardSummary)
def dashboard_summary(
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*DASHBOARD_ROLES)),
):
    users_count = db.query(func.count(User.id)).scalar() or 0
    products_count = db.query(func.count(Product.id)).scalar() or 0

    active_suppliers_count = (
        db.query(func.count(Supplier.id))
        .filter(Supplier.is_active.is_(True))
        .scalar()
        or 0
    )

    purchase_orders_count = (
        db.query(func.count(PurchaseOrder.id)).scalar() or 0
    )

    open_purchase_orders_count = (
        db.query(func.count(PurchaseOrder.id))
        .filter(
            PurchaseOrder.status.in_(
                [
                    "DRAFT",
                    "SUBMITTED",
                    "APPROVED",
                    "ORDERED",
                    "PARTIALLY_RECEIVED",
                ]
            )
        )
        .scalar()
        or 0
    )

    supplier_invoices_count = (
        db.query(func.count(SupplierInvoice.id)).scalar() or 0
    )

    unpaid_total = (
        db.query(
            func.coalesce(func.sum(SupplierInvoice.total_amount), 0)
        )
        .filter(
            SupplierInvoice.status.in_(["RECORDED", "APPROVED"])
        )
        .scalar()
        or Decimal("0.00")
    )

    return DashboardSummary(
        users_count=users_count,
        products_count=products_count,
        active_suppliers_count=active_suppliers_count,
        purchase_orders_count=purchase_orders_count,
        open_purchase_orders_count=open_purchase_orders_count,
        supplier_invoices_count=supplier_invoices_count,
        unpaid_supplier_invoice_total=unpaid_total,
    )


_ADMIN = {"SUPER_ADMIN", "ADMIN"}
SECTION_ROLES = {
    "products_inventory": _ADMIN | {"PRODUCT_MANAGER", "INVENTORY_MANAGER"},
    "orders_by_status": _ADMIN | {"ORDER_MANAGER", "FINANCE_MANAGER"},
    "revenue": _ADMIN | {"FINANCE_MANAGER"},
    "payments_by_status": _ADMIN | {"FINANCE_MANAGER"},
    "low_stock": _ADMIN | {"INVENTORY_MANAGER", "PRODUCT_MANAGER"},
    "returns": _ADMIN | {"ORDER_MANAGER"},
}


def _products_inventory(db: Session) -> dict:
    return {
        "total_products": db.query(func.count(Product.id)).scalar() or 0,
        "active_products": db.query(func.count(Product.id)).filter(Product.is_active.is_(True)).scalar() or 0,
        "units_on_hand": int(db.query(func.coalesce(func.sum(Inventory.quantity_on_hand), 0)).scalar() or 0),
        "units_reserved": int(db.query(func.coalesce(func.sum(Inventory.reserved_quantity), 0)).scalar() or 0),
    }


def _orders_by_status(db: Session) -> dict:
    rows = db.query(Order.status, func.count(Order.id)).group_by(Order.status).all()
    return {status: count for status, count in rows}


def _revenue(db: Session) -> dict:
    paid = ("PAID", "PARTIALLY_REFUNDED")
    gross = db.query(func.coalesce(func.sum(Order.grand_total), 0)).filter(
        Order.payment_status.in_(paid), Order.status != "CANCELLED"
    ).scalar()
    refunded = db.query(func.coalesce(func.sum(Refund.amount), 0)).filter(
        Refund.status == "SUCCEEDED"
    ).scalar()
    gross, refunded = Decimal(str(gross or 0)), Decimal(str(refunded or 0))
    return {
        "gross_revenue": str(gross),
        "refunded": str(refunded),
        "net_revenue": str(gross - refunded),
    }


def _payments_by_status(db: Session) -> dict:
    rows = db.query(Payment.status, func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0)).group_by(Payment.status).all()
    return {status: {"count": count, "amount": str(amount)} for status, count, amount in rows}


def _low_stock(db: Session) -> dict:
    count = db.query(func.count(Inventory.id)).filter(
        Inventory.quantity_on_hand <= Inventory.low_stock_threshold
    ).scalar() or 0
    return {"low_stock_products": count}


def _returns(db: Session) -> dict:
    rows = db.query(ReturnRequest.status, func.count(ReturnRequest.id)).group_by(ReturnRequest.status).all()
    return {status: count for status, count in rows}


SECTION_BUILDERS = {
    "products_inventory": _products_inventory,
    "orders_by_status": _orders_by_status,
    "revenue": _revenue,
    "payments_by_status": _payments_by_status,
    "low_stock": _low_stock,
    "returns": _returns,
}


@router.get("/overview")
def dashboard_overview(
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*DASHBOARD_ROLES)),
):
    """Product/inventory, order, revenue, payment, low-stock and return
    summaries. Each role only receives the sections it is allowed to see."""
    role = current_user.role.value
    return {
        name: builder(db)
        for name, builder in SECTION_BUILDERS.items()
        if role in SECTION_ROLES[name]
    }


@router.get("/{section}")
def dashboard_section(
    section: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*DASHBOARD_ROLES)),
):
    if section not in SECTION_BUILDERS:
        raise HTTPException(status_code=404, detail="Unknown dashboard section")
    if current_user.role.value not in SECTION_ROLES[section]:
        raise HTTPException(status_code=403, detail="You may not view this section")
    return SECTION_BUILDERS[section](db)
