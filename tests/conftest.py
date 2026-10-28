"""Shared fixtures.

Tests run against an in-memory SQLite database (no MySQL needed) and mock
every Stripe call, so they need no network or credentials.
"""
import os
import tempfile

# Environment must be set BEFORE the application is imported.
os.environ["DATABASE_URL"] = "sqlite+pysqlite://"
os.environ["SECRET_KEY"] = "pytest-secret-key-0123456789-0123456789"
os.environ["EMAIL_BACKEND"] = "console"
os.environ["EXPORT_DIR"] = tempfile.mkdtemp(prefix="shopsphere-exports-")
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ.pop("SMS_PROVIDER_URL", None)

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register all tables)
from app.core.config import settings
from app.core.security import hash_password
from app.db.session import Base, SessionLocal
from app.main import app
from app.models import (
    Category, Coupon, Inventory, Product, ProductVariant, ShippingMethod,
    TaxRate, User, UserRole, VariantInventory,
)
from app.services import stripe_gateway

PASSWORD = "Passw0rd!123"
_HASHED = hash_password(PASSWORD)

engine = create_engine(
    "sqlite+pysqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal.configure(bind=engine)

ROLE_EMAILS = {role: f"{role.value.lower()}@example.com" for role in UserRole}


@pytest.fixture(autouse=True)
def fresh_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def make_user(db, email, role=UserRole.CUSTOMER, phone=None, verified=True):
    user = User(
        full_name=f"Test {role.value.title()}",
        email=email,
        phone=phone,
        hashed_password=_HASHED,
        role=role,
        is_active=True,
        is_verified=verified,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login(client, email, password=PASSWORD):
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def users(db):
    """One active user per role, keyed by UserRole."""
    return {role: make_user(db, email, role) for role, email in ROLE_EMAILS.items()}


@pytest.fixture
def h(client, users):
    """h(role) -> auth headers for the seeded user with that role."""
    cache = {}

    def get(role=UserRole.CUSTOMER):
        if role not in cache:
            cache[role] = bearer(login(client, ROLE_EMAILS[role]))
        return cache[role]

    return get


@pytest.fixture
def second_customer(db, client):
    user = make_user(db, "customer2@example.com")
    return user, bearer(login(client, user.email))


@pytest.fixture
def catalog(db):
    category = Category(name="Electronics", slug="electronics")
    db.add(category)
    db.flush()
    product = Product(
        sku="P-1", name="Widget", category="Electronics",
        category_id=category.id, price=Decimal("500.00"),
    )
    db.add(product)
    db.flush()
    db.add(Inventory(product_id=product.id, quantity_on_hand=10, low_stock_threshold=2))
    shipping = ShippingMethod(code="STD", name="Standard", base_fee=Decimal("50.00"))
    tax = TaxRate(name="GST", code="GST18", rate_percent=Decimal("18"), country_code="IN")
    db.add_all([shipping, tax])
    db.commit()
    return SimpleNamespace(
        category_id=category.id, product_id=product.id, shipping_id=shipping.id
    )


@pytest.fixture
def variant_catalog(db, catalog):
    product = Product(
        sku="T-1", name="T-Shirt", category="Apparel",
        price=Decimal("800.00"),
    )
    db.add(product)
    db.flush()
    medium = ProductVariant(product_id=product.id, sku="T-1-M", option_values={"size": "M"})
    large = ProductVariant(
        product_id=product.id, sku="T-1-L", option_values={"size": "L"},
        price=Decimal("900.00"),
    )
    db.add_all([medium, large])
    db.flush()
    db.add(VariantInventory(variant_id=medium.id, quantity_on_hand=5, low_stock_threshold=1))
    db.add(VariantInventory(variant_id=large.id, quantity_on_hand=5, low_stock_threshold=1))
    db.commit()
    return SimpleNamespace(
        product_id=product.id, medium_id=medium.id, large_id=large.id,
        shipping_id=catalog.shipping_id,
    )


@pytest.fixture
def coupon(db):
    now = datetime.now(timezone.utc)
    row = Coupon(
        code="SAVE10", discount_type="PERCENTAGE", discount_value=Decimal("10.00"),
        min_order_amount=Decimal("0.00"), starts_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=1),
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def stripe_mock(monkeypatch):
    """Replace every Stripe call; returns a dict recording what was called."""
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_dummy")
    calls = {"sessions": 0, "refunds": []}

    def fake_session(**kwargs):
        calls["sessions"] += 1
        n = calls["sessions"]
        return {"id": f"cs_test_{n}", "url": f"https://stripe.test/pay/{n}"}

    def fake_refund(**kwargs):
        calls["refunds"].append(kwargs)
        return {"id": f"re_{len(calls['refunds'])}", "status": "succeeded"}

    monkeypatch.setattr(stripe_gateway, "create_checkout_session", fake_session)
    monkeypatch.setattr(stripe_gateway, "create_refund", fake_refund)
    monkeypatch.setattr(stripe_gateway, "construct_event", lambda payload, sig: json.loads(payload))
    return calls


def checkout_payload(shipping_id, **extra):
    payload = {
        "shipping_method_id": shipping_id,
        "shipping_full_name": "Chris Customer",
        "shipping_phone": "+919000000001",
        "shipping_address_line1": "12 MG Road",
        "shipping_city": "Bengaluru",
        "shipping_state": "Karnataka",
        "shipping_postal_code": "560001",
        "shipping_country": "IN",
    }
    payload.update(extra)
    return payload


def place_order(client, headers, product_id, shipping_id, quantity=2, **extra):
    added = client.post(
        "/cart/items", json={"product_id": product_id, "quantity": quantity}, headers=headers
    )
    assert added.status_code == 200, added.text
    response = client.post("/checkout", json=checkout_payload(shipping_id, **extra), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def stripe_event(event_id, session_id, amount_minor, event_type="checkout.session.completed",
                 payment_status="paid", intent="pi_test_1"):
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": {
            "id": session_id, "payment_status": payment_status,
            "amount_total": amount_minor, "currency": "inr", "payment_intent": intent,
        }},
    }


def post_event(client, event):
    return client.post(
        "/payments/webhook", content=json.dumps(event),
        headers={"Stripe-Signature": "t=1,v1=ignored", "Content-Type": "application/json"},
    )


def pay_order(client, headers, order, event_id="evt_paid_1"):
    """Create a checkout session and deliver the success webhook."""
    session = client.post(f"/payments/orders/{order['id']}/checkout-session", headers=headers)
    assert session.status_code == 201, session.text
    payment = session.json()
    amount = int(Decimal(str(order["grand_total"])) * 100)
    result = post_event(client, stripe_event(event_id, payment["provider_reference"], amount))
    assert result.status_code == 200, result.text
    return payment
