"""Insert sample data for manual testing and the Postman collection.

    python -m scripts.seed

Safe to run repeatedly: it exits early if the sample admin already exists.
All sample accounts use the password  ShopSphere#2026  - change it outside
local development.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import (  # noqa: F401  (registers every table)
    Brand, Category, Coupon, Inventory, Product, ProductVariant, Promotion,
    ShippingMethod, Supplier, TaxRate, User, UserRole, VariantInventory,
    Warehouse, WarehouseStock,
)
from app.models.account import Address

PASSWORD = "ShopSphere#2026"

STAFF = [
    ("Sam Super", "superadmin@shopsphere.example.com", UserRole.SUPER_ADMIN),
    ("Ada Admin", "admin@shopsphere.example.com", UserRole.ADMIN),
    ("Pia Product", "product.manager@shopsphere.example.com", UserRole.PRODUCT_MANAGER),
    ("Ian Inventory", "inventory.manager@shopsphere.example.com", UserRole.INVENTORY_MANAGER),
    ("Oli Orders", "order.manager@shopsphere.example.com", UserRole.ORDER_MANAGER),
    ("Sue Support", "support@shopsphere.example.com", UserRole.SUPPORT_AGENT),
    ("Dev Delivery", "delivery.manager@shopsphere.example.com", UserRole.DELIVERY_MANAGER),
    ("Fay Finance", "finance.manager@shopsphere.example.com", UserRole.FINANCE_MANAGER),
]
CUSTOMERS = [
    ("Chris Customer", "customer@shopsphere.example.com", "+919000000001"),
    ("Casey Customer", "customer2@shopsphere.example.com", "+919000000002"),
]


def main() -> None:
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == "admin@shopsphere.example.com").first():
            print("Sample data already present - nothing to do.")
            return

        hashed = hash_password(PASSWORD)
        for name, email, role in STAFF:
            db.add(User(full_name=name, email=email, hashed_password=hashed,
                        role=role, is_active=True, is_verified=True))
        customers = []
        for name, email, phone in CUSTOMERS:
            user = User(full_name=name, email=email, phone=phone,
                        hashed_password=hashed, role=UserRole.CUSTOMER,
                        is_active=True, is_verified=True)
            db.add(user)
            customers.append(user)
        db.flush()

        db.add(Address(
            user_id=customers[0].id, address_type="BOTH", label="Home",
            full_name="Chris Customer", phone="+919000000001",
            line1="12 MG Road", city="Bengaluru", state="Karnataka",
            postal_code="560001", country="IN",
            is_default_shipping=True, is_default_billing=True,
        ))

        electronics = Category(name="Electronics", slug="electronics")
        apparel = Category(name="Apparel", slug="apparel")
        db.add_all([electronics, apparel])
        db.flush()
        db.add(Category(name="Headphones", slug="headphones", parent_id=electronics.id))

        acme = Brand(name="Acme", description="Sample brand")
        db.add(acme)
        db.flush()

        phone = Product(sku="PHONE-001", name="Acme Phone X", category="Electronics",
                        category_id=electronics.id, brand_id=acme.id,
                        price=Decimal("19999.00"), description="Sample smartphone")
        cable = Product(sku="CABLE-001", name="USB-C Cable", category="Electronics",
                        category_id=electronics.id, brand_id=acme.id,
                        price=Decimal("499.00"), description="1m braided cable")
        shirt = Product(sku="SHIRT-001", name="Cotton T-Shirt", category="Apparel",
                        category_id=apparel.id, brand_id=acme.id,
                        price=Decimal("799.00"), description="Available in sizes")
        db.add_all([phone, cable, shirt])
        db.flush()

        db.add(Inventory(product_id=phone.id, quantity_on_hand=25, low_stock_threshold=5))
        db.add(Inventory(product_id=cable.id, quantity_on_hand=200, low_stock_threshold=20))
        db.add(Inventory(product_id=shirt.id, quantity_on_hand=0, low_stock_threshold=0))

        for size, qty in (("M", 40), ("L", 3)):
            variant = ProductVariant(product_id=shirt.id, sku=f"SHIRT-001-{size}",
                                     option_values={"size": size, "color": "Blue"},
                                     price=Decimal("849.00") if size == "L" else None)
            db.add(variant)
            db.flush()
            db.add(VariantInventory(variant_id=variant.id, quantity_on_hand=qty,
                                    low_stock_threshold=5))

        warehouse = Warehouse(code="WH-BLR", name="Bengaluru DC", address="Plot 4, Peenya",
                              city="Bengaluru", state="Karnataka", country="IN",
                              postal_code="560058", capacity_units=10000)
        db.add(warehouse)
        db.flush()
        db.add(WarehouseStock(warehouse_id=warehouse.id, product_id=phone.id, quantity_on_hand=25))

        db.add_all([
            ShippingMethod(code="STANDARD", name="Standard", base_fee=Decimal("49.00"),
                           free_shipping_threshold=Decimal("1000.00"),
                           estimated_min_days=3, estimated_max_days=6),
            ShippingMethod(code="EXPRESS", name="Express", base_fee=Decimal("149.00"),
                           estimated_min_days=1, estimated_max_days=2),
        ])
        db.add(TaxRate(name="GST 18%", code="GST18", rate_percent=Decimal("18.0000"),
                       country_code="IN"))

        now = datetime.now(timezone.utc)
        db.add(Coupon(code="WELCOME10", description="10% off", discount_type="PERCENTAGE",
                      discount_value=Decimal("10.00"), min_order_amount=Decimal("500.00"),
                      usage_limit=1000, starts_at=now - timedelta(days=1),
                      expires_at=now + timedelta(days=365)))
        db.add(Promotion(name="Cable sale", discount_type="PERCENTAGE",
                         discount_value=Decimal("20.00"), target_type="PRODUCT",
                         product_id=cable.id, starts_at=now - timedelta(days=1),
                         expires_at=now + timedelta(days=30)))

        db.add(Supplier(name="Acme Components", contact_person="Raj", email="sales@acme.example.com",
                        phone="+919111111111", address_line1="1 Industrial Area",
                        city="Chennai", state="Tamil Nadu", postal_code="600001", country="IN"))

        db.commit()
        print("Sample data inserted. Staff and customers share the password:", PASSWORD)
        for _, email, _ in STAFF:
            print("  ", email)
        for _, email, _ in CUSTOMERS:
            print("  ", email)
    finally:
        db.close()


if __name__ == "__main__":
    main()
