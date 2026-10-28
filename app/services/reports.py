"""Report builders shared by the JSON report APIs, exports and scheduled jobs.

Each builder returns ``{"report", "columns", "rows", "summary"}`` where rows
are lists aligned with ``columns`` so every export format renders identically.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.category import Category
from app.models.inventory import Inventory
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder
from app.models.refund import Refund
from app.models.return_request import ReturnRequest
from app.models.supplier_invoice import SupplierInvoice
from app.models.warehouse import Warehouse, WarehouseStock

MAX_ROWS = 10000
REVENUE_PAYMENT_STATUSES = ("PAID", "PARTIALLY_REFUNDED")


@dataclass
class ReportFilters:
    date_from: date | None = None
    date_to: date | None = None
    status: str | None = None
    category_id: int | None = None
    warehouse_id: int | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "ReportFilters":
        data = data or {}
        return cls(
            date_from=date.fromisoformat(data["date_from"]) if data.get("date_from") else None,
            date_to=date.fromisoformat(data["date_to"]) if data.get("date_to") else None,
            status=data.get("status"),
            category_id=data.get("category_id"),
            warehouse_id=data.get("warehouse_id"),
        )

    def to_dict(self) -> dict:
        return {
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "status": self.status,
            "category_id": self.category_id,
            "warehouse_id": self.warehouse_id,
        }


def _bounds(column, filters: ReportFilters, query):
    if filters.date_from:
        query = query.filter(column >= datetime.combine(filters.date_from, time.min))
    if filters.date_to:
        query = query.filter(
            column < datetime.combine(filters.date_to + timedelta(days=1), time.min)
        )
    return query


def _s(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _result(name, columns, rows, summary):
    return {"report": name, "columns": columns, "rows": rows, "summary": summary}


def sales_report(db: Session, f: ReportFilters):
    query = db.query(
        func.date(Order.created_at),
        func.count(Order.id),
        func.coalesce(func.sum(Order.subtotal), 0),
        func.coalesce(func.sum(Order.discount_total), 0),
        func.coalesce(func.sum(Order.shipping_total), 0),
        func.coalesce(func.sum(Order.tax_total), 0),
        func.coalesce(func.sum(Order.grand_total), 0),
    ).filter(
        Order.status != "CANCELLED",
        Order.payment_status.in_(REVENUE_PAYMENT_STATUSES),
    )
    query = _bounds(Order.created_at, f, query)
    if f.status:
        query = query.filter(Order.status == f.status.upper())
    data = query.group_by(func.date(Order.created_at)).order_by(func.date(Order.created_at)).all()

    rows = [[_s(d), int(c), _s(sub), _s(dis), _s(ship), _s(tax), _s(total)]
            for d, c, sub, dis, ship, tax, total in data]
    summary = {
        "days": len(rows),
        "orders": sum(r[1] for r in rows),
        "revenue": _s(sum((Decimal(r[6]) for r in rows), Decimal("0.00"))),
    }
    return _result(
        "sales",
        ["Date", "Orders", "Subtotal", "Discounts", "Shipping", "Tax", "Revenue"],
        rows, summary,
    )


def orders_report(db: Session, f: ReportFilters):
    query = db.query(Order)
    query = _bounds(Order.created_at, f, query)
    if f.status:
        query = query.filter(Order.status == f.status.upper())
    orders = query.order_by(Order.created_at.desc()).limit(MAX_ROWS).all()

    by_status: dict[str, int] = {}
    for order in orders:
        by_status[order.status] = by_status.get(order.status, 0) + 1

    rows = [[o.order_number, o.user_id, o.status, o.payment_status,
             _s(o.grand_total), o.currency, _s(o.created_at)] for o in orders]
    return _result(
        "orders",
        ["Order", "Customer ID", "Status", "Payment", "Total", "Currency", "Created"],
        rows, {"count": len(rows), "by_status": by_status},
    )


def inventory_report(db: Session, f: ReportFilters):
    if f.warehouse_id:
        query = (
            db.query(Product, WarehouseStock.quantity_on_hand, Warehouse.code)
            .join(WarehouseStock, WarehouseStock.product_id == Product.id)
            .join(Warehouse, Warehouse.id == WarehouseStock.warehouse_id)
            .filter(WarehouseStock.warehouse_id == f.warehouse_id)
        )
        if f.category_id:
            query = query.filter(Product.category_id == f.category_id)
        data = query.order_by(Product.sku).limit(MAX_ROWS).all()
        rows = [[p.sku, p.name, code, qty, "", qty, ""] for p, qty, code in data]
        return _result(
            "inventory",
            ["SKU", "Product", "Warehouse", "On hand", "Reserved", "Available", "Low stock"],
            rows, {"products": len(rows), "units_on_hand": sum(r[3] for r in rows)},
        )

    query = db.query(Product, Inventory).join(Inventory, Inventory.product_id == Product.id)
    if f.category_id:
        query = query.filter(Product.category_id == f.category_id)
    data = query.order_by(Product.sku).limit(MAX_ROWS).all()
    rows = [
        [p.sku, p.name, "ALL", i.quantity_on_hand, i.reserved_quantity,
         i.available_quantity, "YES" if i.quantity_on_hand <= i.low_stock_threshold else "NO"]
        for p, i in data
    ]
    return _result(
        "inventory",
        ["SKU", "Product", "Warehouse", "On hand", "Reserved", "Available", "Low stock"],
        rows,
        {
            "products": len(rows),
            "units_on_hand": sum(r[3] for r in rows),
            "low_stock_products": sum(1 for r in rows if r[6] == "YES"),
        },
    )


def low_stock_report(db: Session, f: ReportFilters):
    query = (
        db.query(Product, Inventory)
        .join(Inventory, Inventory.product_id == Product.id)
        .filter(Inventory.quantity_on_hand <= Inventory.low_stock_threshold)
    )
    if f.category_id:
        query = query.filter(Product.category_id == f.category_id)
    data = query.order_by(Inventory.quantity_on_hand.asc()).limit(MAX_ROWS).all()
    rows = [[p.sku, p.name, i.quantity_on_hand, i.low_stock_threshold] for p, i in data]
    return _result("low-stock", ["SKU", "Product", "On hand", "Threshold"], rows, {"count": len(rows)})


def payments_report(db: Session, f: ReportFilters):
    query = _bounds(Payment.created_at, f, db.query(Payment))
    if f.status:
        query = query.filter(Payment.status == f.status.upper())
    payments = query.order_by(Payment.created_at.desc()).limit(MAX_ROWS).all()
    totals: dict[str, Decimal] = {}
    for p in payments:
        totals[p.status] = totals.get(p.status, Decimal("0.00")) + Decimal(str(p.amount))
    rows = [[p.id, p.order_id, p.provider, p.status, _s(p.amount), p.currency, _s(p.created_at)]
            for p in payments]
    return _result(
        "payments",
        ["Payment", "Order", "Provider", "Status", "Amount", "Currency", "Created"],
        rows, {"count": len(rows), "amount_by_status": {k: _s(v) for k, v in totals.items()}},
    )


def refunds_report(db: Session, f: ReportFilters):
    query = _bounds(Refund.created_at, f, db.query(Refund))
    if f.status:
        query = query.filter(Refund.status == f.status.upper())
    refunds = query.order_by(Refund.created_at.desc()).limit(MAX_ROWS).all()
    totals: dict[str, Decimal] = {}
    for r in refunds:
        totals[r.status] = totals.get(r.status, Decimal("0.00")) + Decimal(str(r.amount))
    rows = [[r.id, r.order_id, r.payment_id, r.status, _s(r.amount), r.currency, _s(r.created_at)]
            for r in refunds]
    return _result(
        "refunds",
        ["Refund", "Order", "Payment", "Status", "Amount", "Currency", "Created"],
        rows, {"count": len(rows), "amount_by_status": {k: _s(v) for k, v in totals.items()}},
    )


def product_performance_report(db: Session, f: ReportFilters):
    query = (
        db.query(
            Product.sku, Product.name,
            func.coalesce(func.sum(OrderItem.quantity), 0),
            func.coalesce(func.sum(OrderItem.line_total), 0),
        )
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.status != "CANCELLED")
    )
    query = _bounds(Order.created_at, f, query)
    if f.category_id:
        query = query.filter(Product.category_id == f.category_id)
    data = (
        query.group_by(Product.id, Product.sku, Product.name)
        .order_by(func.sum(OrderItem.line_total).desc())
        .limit(MAX_ROWS).all()
    )
    rows = [[sku, name, int(units), _s(rev)] for sku, name, units, rev in data]
    return _result("product-performance", ["SKU", "Product", "Units sold", "Revenue"], rows,
                   {"products": len(rows)})


def category_performance_report(db: Session, f: ReportFilters):
    query = (
        db.query(
            Category.name,
            func.coalesce(func.sum(OrderItem.quantity), 0),
            func.coalesce(func.sum(OrderItem.line_total), 0),
        )
        .join(Product, Product.category_id == Category.id)
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.status != "CANCELLED")
    )
    query = _bounds(Order.created_at, f, query)
    if f.category_id:
        query = query.filter(Category.id == f.category_id)
    data = (
        query.group_by(Category.id, Category.name)
        .order_by(func.sum(OrderItem.line_total).desc()).all()
    )
    rows = [[name, int(units), _s(rev)] for name, units, rev in data]
    return _result("category-performance", ["Category", "Units sold", "Revenue"], rows,
                   {"categories": len(rows)})


def returns_report(db: Session, f: ReportFilters):
    query = _bounds(ReturnRequest.requested_at, f, db.query(ReturnRequest))
    if f.status:
        query = query.filter(ReturnRequest.status == f.status.upper())
    items = query.order_by(ReturnRequest.requested_at.desc()).limit(MAX_ROWS).all()
    by_status: dict[str, int] = {}
    for r in items:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    rows = [[r.id, r.order_id, r.order_item_id, r.quantity, r.status, _s(r.requested_at)] for r in items]
    return _result("returns", ["Return", "Order", "Item", "Qty", "Status", "Requested"], rows,
                   {"count": len(rows), "by_status": by_status})


def purchase_orders_report(db: Session, f: ReportFilters):
    query = _bounds(PurchaseOrder.created_at, f, db.query(PurchaseOrder))
    if f.status:
        query = query.filter(PurchaseOrder.status == f.status.upper())
    items = query.order_by(PurchaseOrder.created_at.desc()).limit(MAX_ROWS).all()
    rows = [[p.po_number, p.supplier_id, p.status, p.currency, _s(p.total_amount), _s(p.created_at)]
            for p in items]
    return _result("purchase-orders", ["PO", "Supplier", "Status", "Currency", "Total", "Created"],
                   rows, {"count": len(rows)})


def supplier_invoices_report(db: Session, f: ReportFilters):
    query = db.query(SupplierInvoice)
    if f.date_from:
        query = query.filter(SupplierInvoice.invoice_date >= f.date_from)
    if f.date_to:
        query = query.filter(SupplierInvoice.invoice_date <= f.date_to)
    if f.status:
        query = query.filter(SupplierInvoice.status == f.status.upper())
    items = query.order_by(SupplierInvoice.invoice_date.desc()).limit(MAX_ROWS).all()
    rows = [[i.invoice_number, i.supplier_id, i.purchase_order_id, i.status,
             _s(i.invoice_date), i.currency, _s(i.total_amount)] for i in items]
    return _result("supplier-invoices",
                   ["Invoice", "Supplier", "PO", "Status", "Date", "Currency", "Total"],
                   rows, {"count": len(rows)})


BUILDERS = {
    "sales": sales_report,
    "orders": orders_report,
    "inventory": inventory_report,
    "low-stock": low_stock_report,
    "payments": payments_report,
    "refunds": refunds_report,
    "product-performance": product_performance_report,
    "category-performance": category_performance_report,
    "returns": returns_report,
    "purchase-orders": purchase_orders_report,
    "supplier-invoices": supplier_invoices_report,
}

_ADMIN = {"SUPER_ADMIN", "ADMIN"}
REPORT_ACCESS = {
    "sales": _ADMIN | {"FINANCE_MANAGER", "ORDER_MANAGER"},
    "orders": _ADMIN | {"ORDER_MANAGER", "FINANCE_MANAGER"},
    "inventory": _ADMIN | {"INVENTORY_MANAGER", "PRODUCT_MANAGER"},
    "low-stock": _ADMIN | {"INVENTORY_MANAGER", "PRODUCT_MANAGER"},
    "payments": _ADMIN | {"FINANCE_MANAGER"},
    "refunds": _ADMIN | {"FINANCE_MANAGER"},
    "product-performance": _ADMIN | {"PRODUCT_MANAGER", "ORDER_MANAGER", "FINANCE_MANAGER"},
    "category-performance": _ADMIN | {"PRODUCT_MANAGER", "ORDER_MANAGER", "FINANCE_MANAGER"},
    "returns": _ADMIN | {"ORDER_MANAGER"},
    "purchase-orders": _ADMIN | {"INVENTORY_MANAGER", "FINANCE_MANAGER"},
    "supplier-invoices": _ADMIN | {"INVENTORY_MANAGER", "FINANCE_MANAGER"},
}


def build_report(db: Session, name: str, filters: ReportFilters) -> dict:
    if name not in BUILDERS:
        raise KeyError(name)
    return BUILDERS[name](db, filters)
