"""Cart → checkout → order → payment → cancellation/refund workflows."""
from decimal import Decimal

from app.models import (
    Inventory, InventoryMovement, Notification, Order, Payment,
    PaymentTransaction, Refund, UserRole, VariantInventory,
)

from tests.conftest import (
    checkout_payload, pay_order, place_order, post_event, stripe_event,
)

CUSTOMER = UserRole.CUSTOMER
FINANCE = UserRole.FINANCE_MANAGER
ORDERS = UserRole.ORDER_MANAGER


# ----------------------------------------------------------------- cart ----

def test_cart_add_update_remove(client, h, catalog):
    headers = h(CUSTOMER)
    cart = client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": 2}, headers=headers).json()
    assert cart["total_items"] == 2 and Decimal(cart["subtotal"]) == Decimal("1000.00")

    cart = client.put(f"/cart/items/{catalog.product_id}", json={"quantity": 3}, headers=headers).json()
    assert cart["total_items"] == 3

    cart = client.delete(f"/cart/items/{catalog.product_id}", headers=headers).json()
    assert cart["items"] == []


def test_cart_rejects_more_than_available_stock(client, h, catalog):
    headers = h(CUSTOMER)
    assert client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": 11}, headers=headers).status_code == 409
    client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": 8}, headers=headers)
    assert client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": 3}, headers=headers).status_code == 409


def test_cart_rejects_unknown_product(client, h, catalog):
    assert client.post("/cart/items", json={"product_id": 9999}, headers=h(CUSTOMER)).status_code == 404


def test_each_customer_has_a_separate_cart(client, h, catalog, second_customer):
    _, other = second_customer
    client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": 1}, headers=h(CUSTOMER))
    assert client.get("/cart", headers=other).json()["items"] == []


def test_cart_summary_includes_shipping_and_tax(client, h, catalog):
    headers = h(CUSTOMER)
    client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": 2}, headers=headers)
    summary = client.get("/cart/summary", params={"shipping_method_id": catalog.shipping_id, "state": "Karnataka"}, headers=headers).json()
    assert Decimal(summary["subtotal"]) == Decimal("1000.00")
    assert Decimal(summary["tax_total"]) == Decimal("180.00")
    assert Decimal(summary["shipping_total"]) == Decimal("50.00")
    assert Decimal(summary["grand_total"]) == Decimal("1230.00")


def test_cart_summary_applies_coupon_and_validates_it(client, h, catalog, coupon):
    headers = h(CUSTOMER)
    client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": 2}, headers=headers)
    summary = client.get("/cart/summary", params={"shipping_method_id": catalog.shipping_id, "coupon_code": "SAVE10"}, headers=headers).json()
    # 1000 - 100 discount = 900, tax 162, shipping 50
    assert Decimal(summary["discount_total"]) == Decimal("100.00")
    assert Decimal(summary["grand_total"]) == Decimal("1112.00")
    bad = client.get("/cart/summary", params={"coupon_code": "NOPE"}, headers=headers)
    assert bad.status_code == 400


def test_empty_cart_summary_is_rejected(client, h, catalog):
    h(CUSTOMER)
    client.get("/cart", headers=h(CUSTOMER))
    assert client.get("/cart/summary", headers=h(CUSTOMER)).status_code == 409


# ------------------------------------------------------------- variants ----

def test_variant_required_when_product_has_variants(client, h, variant_catalog):
    headers = h(CUSTOMER)
    assert client.post("/cart/items", json={"product_id": variant_catalog.product_id}, headers=headers).status_code == 422
    assert client.post("/cart/items", json={"product_id": variant_catalog.product_id, "variant_id": 9999}, headers=headers).status_code == 404


def test_variant_order_uses_variant_price_stock_and_snapshot(client, db, h, variant_catalog):
    headers = h(CUSTOMER)
    client.post("/cart/items", json={"product_id": variant_catalog.product_id, "variant_id": variant_catalog.large_id, "quantity": 2}, headers=headers)
    client.post("/cart/items", json={"product_id": variant_catalog.product_id, "variant_id": variant_catalog.medium_id, "quantity": 1}, headers=headers)
    order = client.post("/checkout", json=checkout_payload(variant_catalog.shipping_id), headers=headers).json()

    by_sku = {item["variant_sku"]: item for item in order["items"]}
    assert Decimal(by_sku["T-1-L"]["unit_price"]) == Decimal("900.00")
    assert Decimal(by_sku["T-1-M"]["unit_price"]) == Decimal("800.00")  # falls back to product price
    assert by_sku["T-1-L"]["variant_options"] == {"size": "L"}
    assert by_sku["T-1-L"]["product_name"] == "T-Shirt"

    db.expire_all()
    stock = {v.variant_id: v.quantity_on_hand for v in db.query(VariantInventory).all()}
    assert stock[variant_catalog.large_id] == 3 and stock[variant_catalog.medium_id] == 4


# ------------------------------------------------------------- checkout ----

def test_checkout_creates_order_and_deducts_stock(client, db, h, catalog):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    assert order["order_number"].startswith("SS-")
    assert order["status"] == "PLACED" and order["payment_status"] == "PENDING"
    assert Decimal(order["grand_total"]) == Decimal("1230.00")
    assert order["items"][0]["product_sku"] == "P-1"
    assert Decimal(order["items"][0]["line_total"]) == Decimal("1180.00")  # 1000 + 180 tax

    db.expire_all()
    assert db.query(Inventory).one().quantity_on_hand == 8
    movement = db.query(InventoryMovement).one()
    assert movement.quantity_change == -2 and "SS-" in movement.reason
    assert client.get("/cart", headers=h(CUSTOMER)).json()["items"] == []


def test_duplicate_checkout_does_not_create_second_order(client, db, h, catalog):
    headers = h(CUSTOMER)
    place_order(client, headers, catalog.product_id, catalog.shipping_id)
    again = client.post("/checkout", json=checkout_payload(catalog.shipping_id), headers=headers)
    assert again.status_code == 409
    db.expire_all()
    assert db.query(Order).count() == 1
    assert db.query(Inventory).one().quantity_on_hand == 8


def test_checkout_blocks_when_stock_vanished_after_adding(client, db, h, catalog):
    headers = h(CUSTOMER)
    client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": 5}, headers=headers)
    stock = db.query(Inventory).one()
    stock.quantity_on_hand = 3
    db.commit()
    response = client.post("/checkout", json=checkout_payload(catalog.shipping_id), headers=headers)
    assert response.status_code == 409
    db.expire_all()
    assert db.query(Inventory).one().quantity_on_hand == 3  # untouched
    assert db.query(Order).count() == 0


def test_checkout_requires_address_and_valid_shipping_method(client, h, catalog):
    headers = h(CUSTOMER)
    client.post("/cart/items", json={"product_id": catalog.product_id}, headers=headers)
    assert client.post("/checkout", json={"shipping_method_id": catalog.shipping_id}, headers=headers).status_code == 422
    assert client.post("/checkout", json=checkout_payload(9999), headers=headers).status_code == 400


def test_checkout_is_customer_only(client, h, catalog):
    assert client.post("/checkout", json=checkout_payload(catalog.shipping_id), headers=h(UserRole.ADMIN)).status_code == 403


def test_checkout_increments_coupon_usage_and_notifies(client, db, h, catalog, coupon):
    place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id, coupon_code="SAVE10")
    db.expire_all()
    from app.models import Coupon
    assert db.query(Coupon).one().used_count == 1
    assert db.query(Notification).filter(Notification.notification_type == "ORDER").count() >= 1


def test_low_stock_alert_goes_to_inventory_managers(client, db, h, users, catalog):
    place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id, quantity=8)  # 10 -> 2 (= threshold)
    db.expire_all()
    alert = db.query(Notification).filter(
        Notification.user_id == users[UserRole.INVENTORY_MANAGER].id,
        Notification.notification_type == "INVENTORY",
    ).first()
    assert alert is not None


# --------------------------------------------------------------- orders ----

def test_customer_sees_only_own_orders(client, h, catalog, second_customer):
    _, other = second_customer
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    assert [o["id"] for o in client.get("/orders", headers=h(CUSTOMER)).json()] == [order["id"]]
    assert client.get("/orders", headers=other).json() == []
    assert client.get(f"/orders/{order['id']}", headers=other).status_code == 404
    assert client.get(f"/orders/{order['id']}/items", headers=other).status_code == 404
    assert client.get(f"/orders/{order['id']}/status-history", headers=other).status_code == 404


def test_order_items_endpoint_returns_sku_snapshot(client, h, catalog):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    items = client.get(f"/orders/{order['id']}/items", headers=h(CUSTOMER))
    assert items.status_code == 200 and items.json()[0]["sku"] == "P-1"


def test_order_search_and_filters(client, h, catalog):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    staff = h(ORDERS)
    assert len(client.get("/orders/admin/all", params={"status": "PLACED"}, headers=staff).json()) == 1
    assert client.get("/orders/admin/all", params={"status": "SHIPPED"}, headers=staff).json() == []
    assert len(client.get("/orders/admin/all", params={"order_number": order["order_number"][3:8]}, headers=staff).json()) == 1
    assert client.get("/orders/admin/all", params={"date_from": "2999-01-01"}, headers=staff).json() == []
    assert len(client.get("/orders", params={"payment_status": "PENDING"}, headers=h(CUSTOMER)).json()) == 1


def test_order_status_transitions_are_enforced(client, h, catalog):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    url = f"/orders/{order['id']}/status"
    staff = h(ORDERS)
    assert client.patch(url, json={"status": "SHIPPED"}, headers=staff).status_code == 409
    assert client.patch(url, json={"status": "BOGUS"}, headers=staff).status_code == 422
    assert client.patch(url, json={"status": "CONFIRMED"}, headers=h(CUSTOMER)).status_code == 403
    assert client.patch(url, json={"status": "CANCELLED"}, headers=staff).status_code == 409  # must use /cancel

    assert client.patch(url, json={"status": "CONFIRMED"}, headers=staff).status_code == 200
    history = client.get(f"/orders/{order['id']}/status-history", headers=h(CUSTOMER)).json()
    assert [row["new_status"] for row in history] == ["PLACED", "CONFIRMED"]


def test_order_item_fulfillment_status(client, h, catalog):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    item_id = order["items"][0]["id"]
    url = f"/orders/{order['id']}/items/{item_id}/fulfillment"
    assert client.patch(url, json={"status": "SHIPPED"}, headers=h(ORDERS)).status_code == 409
    assert client.patch(url, json={"status": "PACKED"}, headers=h(ORDERS)).json()["fulfillment_status"] == "PACKED"
    assert client.patch(url, json={"status": "SHIPPED"}, headers=h(CUSTOMER)).status_code == 403


# --------------------------------------------------------- cancellation ----

def test_cancel_unpaid_order_restores_stock(client, db, h, catalog):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    response = client.post(f"/orders/{order['id']}/cancel", json={"reason": "changed my mind"}, headers=h(CUSTOMER))
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED" and response.json()["refund_id"] is None
    db.expire_all()
    assert db.query(Inventory).one().quantity_on_hand == 10
    assert client.post(f"/orders/{order['id']}/cancel", json={"reason": "again"}, headers=h(CUSTOMER)).status_code == 409


def test_cancel_other_customers_order_is_not_found(client, h, catalog, second_customer):
    _, other = second_customer
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    assert client.post(f"/orders/{order['id']}/cancel", json={"reason": "not mine"}, headers=other).status_code == 404


def test_customer_cannot_cancel_after_processing_starts(client, h, catalog):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    url = f"/orders/{order['id']}/status"
    client.patch(url, json={"status": "CONFIRMED"}, headers=h(ORDERS))
    client.patch(url, json={"status": "PROCESSING"}, headers=h(ORDERS))
    assert client.post(f"/orders/{order['id']}/cancel", json={"reason": "late"}, headers=h(CUSTOMER)).status_code == 409
    assert client.post(f"/orders/{order['id']}/cancel", json={"reason": "stock issue"}, headers=h(ORDERS)).status_code == 200


def test_cancel_paid_order_starts_and_completes_refund(client, db, h, catalog, stripe_mock):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    pay_order(client, h(CUSTOMER), order)
    response = client.post(f"/orders/{order['id']}/cancel", json={"reason": "no longer needed"}, headers=h(CUSTOMER)).json()
    assert response["refund_id"] and response["refund_status"] == "SUCCEEDED"
    assert stripe_mock["refunds"][0]["amount"] == Decimal("1230.00")

    db.expire_all()
    assert db.query(Inventory).one().quantity_on_hand == 10
    assert db.query(Order).one().payment_status == "REFUNDED"
    assert db.query(Payment).one().status == "REFUNDED"


def test_cancel_unpaid_order_cancels_open_payment_session(client, db, h, catalog, stripe_mock):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    client.post(f"/payments/orders/{order['id']}/checkout-session", headers=h(CUSTOMER))
    client.post(f"/orders/{order['id']}/cancel", json={"reason": "oops"}, headers=h(CUSTOMER))
    db.expire_all()
    assert db.query(Payment).one().status == "CANCELLED"
    assert client.post(f"/payments/orders/{order['id']}/checkout-session", headers=h(CUSTOMER)).status_code == 409


# -------------------------------------------------------------- payments ----

def test_checkout_session_is_idempotent(client, db, h, catalog, stripe_mock):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    first = client.post(f"/payments/orders/{order['id']}/checkout-session", headers=h(CUSTOMER))
    second = client.post(f"/payments/orders/{order['id']}/checkout-session", headers=h(CUSTOMER))
    assert first.status_code == 201 and second.json()["id"] == first.json()["id"]
    assert stripe_mock["sessions"] == 1
    db.expire_all()
    assert db.query(Payment).count() == 1


def test_payment_requires_ownership(client, h, catalog, stripe_mock, second_customer):
    _, other = second_customer
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    assert client.post(f"/payments/orders/{order['id']}/checkout-session", headers=other).status_code == 404


def test_payment_without_stripe_configured_returns_503(client, h, catalog, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "stripe_secret_key", None)
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    assert client.post(f"/payments/orders/{order['id']}/checkout-session", headers=h(CUSTOMER)).status_code == 503


def test_webhook_marks_payment_and_order_paid(client, db, h, catalog, stripe_mock):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    payment = pay_order(client, h(CUSTOMER), order)

    detail = client.get(f"/orders/{order['id']}", headers=h(CUSTOMER)).json()
    assert detail["payment_status"] == "PAID" and detail["status"] == "CONFIRMED"
    assert client.get(f"/payments/{payment['id']}", headers=h(CUSTOMER)).json()["status"] == "SUCCEEDED"
    db.expire_all()
    assert db.query(Payment).one().provider_payment_intent == "pi_test_1"
    assert db.query(Notification).filter(Notification.notification_type == "PAYMENT").count() >= 1


def test_webhook_replay_is_processed_once(client, db, h, catalog, stripe_mock):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    payment = pay_order(client, h(CUSTOMER), order, event_id="evt_same")

    event = stripe_event("evt_same", payment["provider_reference"], 123000)
    replay = post_event(client, event)
    assert replay.status_code == 200 and replay.json()["outcome"] == "duplicate"

    # A different event id for an already-paid payment is also harmless.
    other = post_event(client, stripe_event("evt_other", payment["provider_reference"], 123000))
    assert other.json()["outcome"] == "already_paid"
    db.expire_all()
    assert db.query(PaymentTransaction).filter(PaymentTransaction.provider_event_id == "evt_same").count() == 1
    assert db.query(Order).one().payment_status == "PAID"


def test_webhook_amount_mismatch_is_not_applied(client, db, h, catalog, stripe_mock):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    payment = client.post(f"/payments/orders/{order['id']}/checkout-session", headers=h(CUSTOMER)).json()
    result = post_event(client, stripe_event("evt_bad", payment["provider_reference"], 100))
    assert result.json()["outcome"] == "amount_mismatch"
    db.expire_all()
    assert db.query(Order).one().payment_status == "PENDING"


def test_webhook_failure_marks_payment_failed_and_allows_retry(client, db, h, catalog, stripe_mock):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    payment = client.post(f"/payments/orders/{order['id']}/checkout-session", headers=h(CUSTOMER)).json()
    event = stripe_event("evt_exp", payment["provider_reference"], 123000, event_type="checkout.session.expired", payment_status="unpaid")
    assert post_event(client, event).json()["outcome"] == "payment_failed"
    db.expire_all()
    assert db.query(Payment).one().status == "EXPIRED"
    assert db.query(Order).one().payment_status == "FAILED"
    retry = client.post(f"/payments/orders/{order['id']}/checkout-session", headers=h(CUSTOMER))
    assert retry.status_code == 201 and retry.json()["provider_reference"] == "cs_test_2"


def test_webhook_for_unknown_session_is_acknowledged(client, stripe_mock):
    result = post_event(client, stripe_event("evt_x", "cs_unknown", 100))
    assert result.status_code == 200 and result.json()["outcome"] == "unknown_payment"


def test_webhook_rejects_invalid_signature(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test_secret")
    response = client.post("/payments/webhook", content=b"{}", headers={"Stripe-Signature": "t=1,v1=deadbeef"})
    assert response.status_code == 400


def test_webhook_accepts_correctly_signed_payload(client, db, h, catalog, monkeypatch):
    """Uses the REAL stripe signature verification (no mock)."""
    import hashlib, hmac, json, time
    from app.core.config import settings
    from app.services import stripe_gateway

    secret = "whsec_real_check"
    monkeypatch.setattr(settings, "stripe_webhook_secret", secret)
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_dummy")
    monkeypatch.setattr(stripe_gateway, "create_checkout_session",
                        lambda **kw: {"id": "cs_real_1", "url": "https://stripe.test/pay"})

    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    client.post(f"/payments/orders/{order['id']}/checkout-session", headers=h(CUSTOMER))

    body = json.dumps(stripe_event("evt_signed", "cs_real_1", 123000))
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), f"{stamp}.{body}".encode(), hashlib.sha256).hexdigest()
    response = client.post("/payments/webhook", content=body,
                           headers={"Stripe-Signature": f"t={stamp},v1={signature}", "Content-Type": "application/json"})
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "payment_succeeded"


def test_payment_history_is_private(client, h, catalog, stripe_mock, second_customer):
    _, other = second_customer
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    pay_order(client, h(CUSTOMER), order)
    assert len(client.get("/payments", headers=h(CUSTOMER)).json()) == 1
    assert client.get("/payments", headers=other).json() == []
    assert len(client.get("/payments", headers=h(FINANCE)).json()) == 1


# --------------------------------------------------------------- refunds ----

def _paid_order(client, h, catalog):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    return order, pay_order(client, h(CUSTOMER), order)


def _refund(client, h, payment_id, amount, reason="damaged"):
    return client.post("/refunds", json={"payment_id": payment_id, "amount": amount, "reason": reason}, headers=h(CUSTOMER))


def test_partial_then_full_refund_flow(client, db, h, catalog, stripe_mock):
    order, payment = _paid_order(client, h, catalog)

    requested = _refund(client, h, payment["id"], "200.00")
    assert requested.status_code == 201 and requested.json()["status"] == "REQUESTED"
    refund_id = requested.json()["id"]

    assert client.post(f"/refunds/{refund_id}/process", headers=h(FINANCE)).status_code == 409  # not approved yet
    assert client.patch(f"/refunds/{refund_id}/decision", json={"status": "APPROVED"}, headers=h(CUSTOMER)).status_code == 403
    assert client.patch(f"/refunds/{refund_id}/decision", json={"status": "APPROVED"}, headers=h(FINANCE)).status_code == 200
    assert client.post(f"/refunds/{refund_id}/process", headers=h(CUSTOMER)).status_code == 403

    done = client.post(f"/refunds/{refund_id}/process", headers=h(FINANCE)).json()
    assert done["status"] == "SUCCEEDED"
    assert stripe_mock["refunds"][0]["idempotency_key"] == f"refund-{refund_id}"
    assert stripe_mock["refunds"][0]["payment_intent_id"] == "pi_test_1"
    db.expire_all()
    assert db.query(Order).one().payment_status == "PARTIALLY_REFUNDED"

    # Processing the same refund twice never hits the gateway twice.
    assert client.post(f"/refunds/{refund_id}/process", headers=h(FINANCE)).status_code == 409
    assert len(stripe_mock["refunds"]) == 1

    # Cannot exceed what is left (1230 - 200 = 1030).
    assert _refund(client, h, payment["id"], "1100.00").status_code == 400
    rest = _refund(client, h, payment["id"], "1030.00").json()
    client.patch(f"/refunds/{rest['id']}/decision", json={"status": "APPROVED"}, headers=h(FINANCE))
    assert client.post(f"/refunds/{rest['id']}/process", headers=h(FINANCE)).json()["status"] == "SUCCEEDED"
    db.expire_all()
    assert db.query(Order).one().payment_status == "REFUNDED"
    assert db.query(Payment).one().status == "REFUNDED"


def test_rejected_refund_cannot_be_processed(client, h, catalog, stripe_mock):
    _, payment = _paid_order(client, h, catalog)
    refund = _refund(client, h, payment["id"], "100.00").json()
    client.patch(f"/refunds/{refund['id']}/decision", json={"status": "REJECTED", "decision_note": "no"}, headers=h(FINANCE))
    assert client.post(f"/refunds/{refund['id']}/process", headers=h(FINANCE)).status_code == 409
    assert stripe_mock["refunds"] == []


def test_refund_request_requires_paid_payment_and_ownership(client, h, catalog, stripe_mock, second_customer):
    _, other = second_customer
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    unpaid = client.post(f"/payments/orders/{order['id']}/checkout-session", headers=h(CUSTOMER)).json()
    assert _refund(client, h, unpaid["id"], "10.00").status_code == 400

    _, payment = _paid_order_for_second(client, h, catalog)
    stolen = client.post("/refunds", json={"payment_id": payment["id"], "amount": "10.00", "reason": "mine now"}, headers=other)
    assert stolen.status_code == 403


def _paid_order_for_second(client, h, catalog):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id, quantity=1)
    return order, pay_order(client, h(CUSTOMER), order, event_id="evt_second")


def test_gateway_failure_leaves_refund_retryable(client, db, h, catalog, stripe_mock, monkeypatch):
    from app.services import stripe_gateway
    _, payment = _paid_order(client, h, catalog)
    refund = _refund(client, h, payment["id"], "50.00").json()
    client.patch(f"/refunds/{refund['id']}/decision", json={"status": "APPROVED"}, headers=h(FINANCE))

    def boom(**kwargs):
        raise stripe_gateway.GatewayError("down")
    good = stripe_gateway.create_refund
    monkeypatch.setattr(stripe_gateway, "create_refund", boom)
    failed = client.post(f"/refunds/{refund['id']}/process", headers=h(FINANCE)).json()
    assert failed["status"] == "FAILED"

    monkeypatch.setattr(stripe_gateway, "create_refund", good)
    assert client.post(f"/refunds/{refund['id']}/process", headers=h(FINANCE)).json()["status"] == "SUCCEEDED"


def test_cod_style_payment_needs_manual_settlement(client, db, h, catalog, stripe_mock):
    _, payment = _paid_order(client, h, catalog)
    row = db.query(Payment).one()
    row.provider_payment_intent = None
    db.commit()
    refund = _refund(client, h, payment["id"], "20.00").json()
    client.patch(f"/refunds/{refund['id']}/decision", json={"status": "APPROVED"}, headers=h(FINANCE))
    assert client.post(f"/refunds/{refund['id']}/process", headers=h(FINANCE)).status_code == 409
    manual = client.post(f"/refunds/{refund['id']}/process", json={"manual_settlement": True}, headers=h(FINANCE))
    assert manual.status_code == 200 and manual.json()["status"] == "SUCCEEDED"
    assert stripe_mock["refunds"] == []
