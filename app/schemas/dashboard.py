
from decimal import Decimal

from pydantic import BaseModel


class DashboardSummary(BaseModel):
    users_count: int
    products_count: int
    active_suppliers_count: int
    purchase_orders_count: int
    open_purchase_orders_count: int
    supplier_invoices_count: int
    unpaid_supplier_invoice_total: Decimal
