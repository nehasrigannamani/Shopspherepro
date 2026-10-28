"""Central role → permission matrix used for documentation and checks."""
from app.models.user import UserRole

R = UserRole

ADMINS = (R.SUPER_ADMIN, R.ADMIN)
STAFF_ROLES = tuple(role for role in UserRole if role != R.CUSTOMER)

ROLE_PERMISSIONS: dict[UserRole, list[str]] = {
    R.SUPER_ADMIN: ["*"],
    R.ADMIN: [
        "users:manage", "products:manage", "orders:manage",
        "inventory:read", "payments:read", "reports:read",
        "audit:read", "suppliers:manage",
    ],
    R.PRODUCT_MANAGER: [
        "products:manage", "categories:manage", "brands:manage",
        "pricing:manage", "reports:read",
    ],
    R.INVENTORY_MANAGER: [
        "inventory:manage", "warehouses:manage", "transfers:manage",
        "purchase_orders:manage", "reports:read",
    ],
    R.ORDER_MANAGER: [
        "orders:manage", "returns:manage", "exchanges:manage",
        "reports:read",
    ],
    R.SUPPORT_AGENT: [
        "tickets:manage", "questions:answer", "reviews:moderate",
        "notifications:send",
    ],
    R.DELIVERY_MANAGER: [
        "shipments:manage", "deliveries:manage", "tracking:manage",
    ],
    R.FINANCE_MANAGER: [
        "payments:read", "refunds:manage", "invoices:manage",
        "reports:read", "cod:reconcile",
    ],
    R.CUSTOMER: [
        "catalog:read", "cart:manage", "orders:own", "payments:own",
        "returns:own", "reviews:own", "profile:own", "addresses:own",
    ],
}
