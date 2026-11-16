"""Payment reconciliation, timeouts, duplicate capture, refund idempotency, exceptions, settlements."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import Payment, Refund, UserRole
from app.services import stripe_gateway
from app.services.reservations import now_naive

from tests.conftest import pay_order, place_order, post_event, stripe_event

CUSTOMER = UserRole.CUSTOMER
FINANCE = UserRole.FINANCE_MANAGER
ADMIN = UserRole.ADMIN
V2 = "/v2"


class Provider:
    """Fake gateway state keyed by checkout session id."""

    def __init__(self):
        self.sessions, self.expired = {}, []

    def set(self, session_id, amount_minor, paid=False, expired=False, intent="pi_p1"):
        self.sessions[session_id] = {
            "id": session_id, "payment_status": "paid" if paid else "unpaid",
            "status": "complete" if paid else ("expired" if expired else "open"),
            "amount_total": amount_minor, "currency": "inr", "payment_intent": intent,
        }


@pytest.fixture
def provider(stripe_mock, monkeypatch):
    p = Provider()

    def retrieve(session_id):
        if session_id not in p.sessions:
            raise stripe_gateway.GatewayError("gateway down")
        return dict(p.sessions[session_id])

    def expire(session_id):
        p.expired.append(session_id)
        if session_id in p.sessions and p.sessions[session_id]["payment_status"] != "paid":
            p.sessions[session_id]["status"] = "expired"

    monkeypatch.setattr(stripe_gateway, "retrieve_checkout_session", retrieve)
    monkeypatch.setattr(stripe_gateway, "expire_checkout_session", expire)
    return p


def minor(order):
    return int(Decimal(str(order["grand_total"])) * 100)


def pending_payment(client, h, catalog, quantity=2):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id, quantity=quantity)
    payment = client.post(f"/payments/orders/{order['id']}/checkout-session", headers=h(CUSTOMER))
    assert payment.status_code == 201, payment.text
    return order, payment.json()


def age_payment(db, payment_id, minutes):
    db.expire_all()
    db.get(Payment, payment_id).created_at = now_naive() - timedelta(minutes=minutes)
    db.commit()


def payment_row(db, payment_id):
    db.expire_all()
    return db.get(Payment, payment_id)


def order_json(client, h, order_id):
    return client.get(f"/orders/{order_id}", headers=h(CUSTOMER)).json()


# ------------------------------------------------------------------ reconciliation
def test_reconcile_applies_a_missed_paid_callback(client, db, h, catalog, provider):
    order, payment = pending_payment(client, h, catalog)
    provider.set(payment["provider_reference"], minor(order), paid=True, intent="pi_missed")
    first = client.post(f"{V2}/payments/{payment['id']}/reconcile", headers=h(FINANCE))
    assert first.status_code == 200, first.text
    assert first.json()["outcome"] == "APPLIED_PAID"
    assert first.json()["local_status_before"] == "PENDING" and first.json()["local_status_after"] == "SUCCEEDED"
    assert payment_row(db, payment["id"]).provider_payment_intent == "pi_missed"
    body = order_json(client, h, order["id"])
    assert body["payment_status"] == "PAID" and body["status"] == "CONFIRMED"
    assert client.post(f"{V2}/payments/{payment['id']}/reconcile", headers=h(FINANCE)).json()["outcome"] == "IN_SYNC"


def test_reconcile_marks_an_expired_session_failed(client, db, h, catalog, provider):
    order, payment = pending_payment(client, h, catalog)
    provider.set(payment["provider_reference"], minor(order), expired=True)
    result = client.post(f"{V2}/payments/{payment['id']}/reconcile", headers=h(FINANCE)).json()
    assert result["outcome"] == "APPLIED_EXPIRED"
    assert payment_row(db, payment["id"]).status == "EXPIRED"
    assert order_json(client, h, order["id"])["payment_status"] == "FAILED"


def test_abandoned_payment_times_out_and_can_be_retried(client, db, h, catalog, provider):
    order, payment = pending_payment(client, h, catalog)
    provider.set(payment["provider_reference"], minor(order))
    url = f"{V2}/payments/{payment['id']}/reconcile"
    assert client.post(url, headers=h(FINANCE)).json()["outcome"] == "IN_SYNC"          # still within the window
    age_payment(db, payment["id"], 120)
    result = client.post(url, headers=h(FINANCE)).json()
    assert result["outcome"] == "TIMED_OUT" and result["local_status_after"] == "TIMED_OUT"
    assert payment["provider_reference"] in provider.expired                              # session closed at the gateway
    retry = client.post(f"/payments/orders/{order['id']}/checkout-session", headers=h(CUSTOMER))
    assert retry.status_code == 201 and retry.json()["id"] != payment["id"]
    # next pass settles the label once the gateway reports the session as expired
    assert client.post(url, headers=h(FINANCE)).json()["outcome"] == "APPLIED_EXPIRED"


def test_batch_reconcile_only_touches_stale_pending_payments(client, db, h, catalog, provider):
    old_order, old = pending_payment(client, h, catalog, quantity=1)
    new_order, fresh = pending_payment(client, h, catalog, quantity=1)
    for order, payment in ((old_order, old), (new_order, fresh)):
        provider.set(payment["provider_reference"], minor(order))
    age_payment(db, old["id"], 180)
    result = client.post(f"{V2}/payments/reconcile-pending", json={"older_than_minutes": 60}, headers=h(FINANCE))
    assert result.status_code == 200
    assert result.json() == {"checked": 1, "outcomes": {"TIMED_OUT": 1}}
    assert payment_row(db, fresh["id"]).status == "PENDING"
    assert client.post(f"{V2}/payments/reconcile-pending", json={"limit": 0}, headers=h(FINANCE)).status_code == 422


def test_gateway_outage_changes_nothing(client, db, h, catalog, provider):
    order, payment = pending_payment(client, h, catalog)       # provider knows nothing -> "gateway down"
    result = client.post(f"{V2}/payments/{payment['id']}/reconcile", headers=h(FINANCE)).json()
    assert result["outcome"] == "PROVIDER_UNAVAILABLE"
    assert payment_row(db, payment["id"]).status == "PENDING"


def test_local_paid_but_provider_unpaid_is_flagged_not_changed(client, db, h, catalog, provider):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    payment = pay_order(client, h(CUSTOMER), order)
    provider.set(payment["provider_reference"], minor(order))                 # gateway says: open, unpaid
    for _ in range(2):
        assert client.post(f"{V2}/payments/{payment['id']}/reconcile", headers=h(FINANCE)).json()["outcome"] == "DIVERGENCE"
    assert payment_row(db, payment["id"]).status == "SUCCEEDED"
    listing = client.get(f"{V2}/finance/exceptions?exc_type=STATUS_DIVERGENCE", headers=h(FINANCE)).json()
    assert listing["total"] == 1 and listing["items"][0]["severity"] == "HIGH"   # raised once, not twice


def test_reconcile_unknown_payment_is_404(client, h):
    assert client.post(f"{V2}/payments/99999/reconcile", headers=h(FINANCE)).status_code == 404


# ------------------------------------------------------------------ duplicate capture / exceptions
def make_duplicate(client, db, h, catalog, stripe_mock):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    pay_order(client, h(CUSTOMER), order)
    db.expire_all()
    first = db.query(Payment).filter(Payment.order_id == order["id"]).one()
    dup = Payment(order_id=order["id"], user_id=first.user_id, provider="STRIPE", provider_reference="cs_dup_1",
                  amount=first.amount, currency=first.currency, status="PENDING")
    db.add(dup)
    db.commit()
    return order, dup.id


def test_duplicate_capture_is_kept_refunded_and_flagged(client, db, h, catalog, stripe_mock):
    order, dup_id = make_duplicate(client, db, h, catalog, stripe_mock)
    event = stripe_event("evt_dup_1", "cs_dup_1", minor(order), intent="pi_dup")
    assert post_event(client, event).json()["outcome"] == "duplicate_capture"
    assert post_event(client, event).json()["outcome"] == "duplicate"            # same event replayed
    again = stripe_event("evt_dup_2", "cs_dup_1", minor(order), intent="pi_dup")
    assert post_event(client, again).json()["outcome"] == "already_paid"          # new event id, same payment

    dup = payment_row(db, dup_id)
    assert dup.status == "DUPLICATE_PAID"
    refunds = db.query(Refund).filter(Refund.payment_id == dup_id).all()
    assert len(refunds) == 1 and refunds[0].status == "APPROVED"
    assert Decimal(str(refunds[0].amount)) == Decimal(str(order["grand_total"]))
    assert order_json(client, h, order["id"])["payment_status"] == "PAID"

    queue = client.get(f"{V2}/finance/exceptions?status=OPEN", headers=h(FINANCE)).json()
    assert [e["exc_type"] for e in queue["items"]] == ["DUPLICATE_CAPTURE"]

    done = client.post(f"{V2}/refunds/process-approved", json={}, headers=h(FINANCE))
    assert done.status_code == 200 and done.json()["succeeded"] == 1
    assert payment_row(db, dup_id).status == "REFUNDED"
    assert order_json(client, h, order["id"])["payment_status"] == "PAID"        # real payment untouched
    assert client.post(f"{V2}/refunds/process-approved", json={}, headers=h(FINANCE)).json()["attempted"] == 0


def test_amount_mismatch_raises_one_exception(client, db, h, catalog, provider):
    order, payment = pending_payment(client, h, catalog)
    event = stripe_event("evt_bad_1", payment["provider_reference"], minor(order) + 100)
    assert post_event(client, event).json()["outcome"] == "amount_mismatch"
    post_event(client, event)
    assert payment_row(db, payment["id"]).status == "AMOUNT_MISMATCH"
    queue = client.get(f"{V2}/finance/exceptions?exc_type=AMOUNT_MISMATCH", headers=h(FINANCE)).json()
    assert queue["total"] == 1 and queue["items"][0]["payment_id"] == payment["id"]
    assert "pi_" not in str(queue)                                                  # no gateway identifiers leak


def test_late_payment_after_cancellation_is_queued_for_review(client, db, h, catalog, provider):
    order, payment = pending_payment(client, h, catalog)
    assert client.post(f"/orders/{order['id']}/cancel", headers=h(CUSTOMER), json={"reason": "changed my mind"}).status_code == 200
    event = stripe_event("evt_late_1", payment["provider_reference"], minor(order))
    post_event(client, event)
    queue = client.get(f"{V2}/finance/exceptions?exc_type=LATE_PAYMENT_AFTER_CANCEL", headers=h(FINANCE)).json()
    assert queue["total"] == 1
    assert db.query(Refund).filter(Refund.order_id == order["id"]).count() == 1


def test_exception_review_workflow(client, db, h, catalog, provider):
    order, payment = pending_payment(client, h, catalog)
    post_event(client, stripe_event("evt_bad_2", payment["provider_reference"], minor(order) + 1))
    exc = client.get(f"{V2}/finance/exceptions", headers=h(FINANCE)).json()["items"][0]
    url = f"{V2}/finance/exceptions/{exc['id']}"
    assert client.get(url, headers=h(FINANCE)).json()["status"] == "OPEN"
    assert client.get(f"{V2}/finance/exceptions/99999", headers=h(FINANCE)).status_code == 404

    claimed = client.post(f"{url}/claim", headers=h(FINANCE))
    assert claimed.status_code == 200 and claimed.json()["status"] == "IN_REVIEW" and claimed.json()["assigned_to"]
    assert client.post(f"{url}/claim", headers=h(ADMIN)).status_code == 409

    assert client.post(f"{url}/resolve", json={"resolution": "RESOLVED"}, headers=h(FINANCE)).status_code == 422
    assert client.post(f"{url}/resolve", json={"resolution": "MAYBE", "note": "unsure"}, headers=h(FINANCE)).status_code == 422
    closed = client.post(f"{url}/resolve", json={"resolution": "RESOLVED", "note": "Customer paid the difference"},
                         headers=h(FINANCE))
    assert closed.status_code == 200 and closed.json()["resolved_by"] and closed.json()["resolved_at"]
    assert client.post(f"{url}/resolve", json={"resolution": "DISMISSED", "note": "again"}, headers=h(FINANCE)).status_code == 409
    assert client.get(f"{V2}/finance/exceptions?status=OPEN", headers=h(FINANCE)).json()["total"] == 0
    assert client.get(f"{V2}/finance/exceptions?status=BOGUS", headers=h(FINANCE)).status_code == 422
    assert client.get(f"{V2}/finance/exceptions?page_size=101", headers=h(FINANCE)).status_code == 422


@pytest.mark.parametrize("role, expected", [
    (CUSTOMER, 403), (UserRole.SUPPORT_AGENT, 403), (UserRole.ORDER_MANAGER, 403),
    (UserRole.SELLER, 403), (FINANCE, 200), (ADMIN, 200), (UserRole.SUPER_ADMIN, 200),
])
def test_finance_endpoints_role_matrix(client, h, role, expected):
    for path in ("/v2/finance/exceptions", "/v2/finance/settlements"):
        assert client.get(path, headers=h(role)).status_code == expected
    assert client.post("/v2/payments/reconcile-pending", json={}, headers=h(role)).status_code == expected
    assert client.post("/v2/refunds/process-approved", json={}, headers=h(role)).status_code == expected


# ------------------------------------------------------------------ refunds (v2)
def test_multiple_partial_refunds_up_to_the_paid_amount(client, db, h, catalog, stripe_mock):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    payment = pay_order(client, h(CUSTOMER), order)
    total = Decimal(str(order["grand_total"]))
    ask = lambda amount, **kw: client.post(f"{V2}/refunds", headers=h(CUSTOMER), json={
        "payment_id": payment["id"], "amount": str(amount), "reason": "Item arrived damaged"}, **kw)

    assert ask(Decimal("100.00")).status_code == 201
    assert ask(Decimal("200.00")).status_code == 201
    summary = client.get(f"{V2}/orders/{order['id']}/payment-summary", headers=h(CUSTOMER)).json()
    assert Decimal(summary["captured"]) == total
    assert Decimal(summary["refunds_in_progress"]) == Decimal("300.00")
    assert Decimal(summary["refundable_remaining"]) == total - 300 and len(summary["refunds"]) == 2
    assert ask(total - 299).status_code == 409                       # one rupee too many
    assert ask(total - 300).status_code == 201                       # exactly the rest
    assert ask(Decimal("0.01")).status_code == 409                   # nothing left


def test_refund_request_validation_and_ownership(client, h, catalog, stripe_mock, second_customer):
    _, other = second_customer
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    unpaid = client.post(f"/payments/orders/{order['id']}/checkout-session", headers=h(CUSTOMER)).json()
    body = {"payment_id": unpaid["id"], "amount": "10.00", "reason": "Not needed"}
    assert client.post(f"{V2}/refunds", json=body, headers=h(CUSTOMER)).status_code == 409   # not paid
    paid_order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id, quantity=1)
    paid = pay_order(client, h(CUSTOMER), paid_order)
    ok = {"payment_id": paid["id"], "amount": "10.00", "reason": "Not needed"}
    assert client.post(f"{V2}/refunds", json=ok, headers=other).status_code == 404            # someone else's payment
    assert client.post(f"{V2}/refunds", json={**ok, "amount": "0"}, headers=h(CUSTOMER)).status_code == 422
    assert client.post(f"{V2}/refunds", json={**ok, "amount": "-5"}, headers=h(CUSTOMER)).status_code == 422
    assert client.post(f"{V2}/refunds", json={**ok, "amount": "1.999"}, headers=h(CUSTOMER)).status_code == 422
    assert client.post(f"{V2}/refunds", json={**ok, "reason": "x"}, headers=h(CUSTOMER)).status_code == 422
    assert client.post(f"{V2}/refunds", json={**ok, "payment_id": 99999}, headers=h(CUSTOMER)).status_code == 404
    assert client.post(f"{V2}/refunds", json=ok, headers=h(UserRole.SUPPORT_AGENT)).status_code == 403
    assert client.post(f"{V2}/refunds", json=ok, headers=h(FINANCE)).status_code == 201       # finance may act for the customer


def test_refund_request_is_idempotent(client, db, h, catalog, stripe_mock):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    payment = pay_order(client, h(CUSTOMER), order)
    headers = {**h(CUSTOMER), "Idempotency-Key": "refund-key-0001"}
    body = {"payment_id": payment["id"], "amount": "100.00", "reason": "Damaged item"}
    first = client.post(f"{V2}/refunds", json=body, headers=headers)
    second = client.post(f"{V2}/refunds", json=body, headers=headers)      # double click / network retry
    assert first.status_code == 201 and second.status_code == 201
    assert second.headers.get("Idempotent-Replay") == "true" and second.json()["id"] == first.json()["id"]
    assert db.query(Refund).filter(Refund.payment_id == payment["id"]).count() == 1
    assert client.post(f"{V2}/refunds", json={**body, "amount": "150.00"}, headers=headers).status_code == 422
    assert client.post(f"{V2}/refunds", json=body, headers={**h(CUSTOMER), "Idempotency-Key": "short"}).status_code == 422


def test_process_approved_refunds_sends_them_once(client, db, h, catalog, stripe_mock):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    payment = pay_order(client, h(CUSTOMER), order)
    refund = client.post(f"{V2}/refunds", headers=h(CUSTOMER),
                         json={"payment_id": payment["id"], "amount": "100.00", "reason": "Damaged item"}).json()
    nothing = client.post(f"{V2}/refunds/process-approved", json={}, headers=h(FINANCE)).json()
    assert nothing["attempted"] == 0                                           # REQUESTED is not approved yet
    client.patch(f"/refunds/{refund['id']}/decision", json={"status": "APPROVED"}, headers=h(FINANCE))
    done = client.post(f"{V2}/refunds/process-approved", json={}, headers=h(FINANCE)).json()
    assert done == {"attempted": 1, "succeeded": 1, "failed": 0, "skipped": 0}
    assert len(stripe_mock["refunds"]) == 1
    assert client.post(f"{V2}/refunds/process-approved", json={}, headers=h(FINANCE)).json()["attempted"] == 0
    assert len(stripe_mock["refunds"]) == 1                                    # never sent twice


# ------------------------------------------------------------------ history
def test_payment_history_hides_raw_payloads_and_staff_only_events(client, h, catalog, provider, second_customer):
    _, other = second_customer
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    payment = pay_order(client, h(CUSTOMER), order)
    client.post(f"{V2}/refunds", headers=h(CUSTOMER),
                json={"payment_id": payment["id"], "amount": "50.00", "reason": "Small refund"})
    provider.set(payment["provider_reference"], minor(order), paid=True)
    client.post(f"{V2}/payments/{payment['id']}/reconcile", headers=h(FINANCE))

    mine = client.get(f"{V2}/payments/{payment['id']}/history", headers=h(CUSTOMER))
    staff = client.get(f"{V2}/payments/{payment['id']}/history", headers=h(FINANCE))
    assert mine.status_code == 200 and staff.status_code == 200
    kinds = {e["kind"] for e in mine.json()}
    assert kinds == {"TRANSACTION", "REFUND"}
    assert {"TRANSACTION", "REFUND", "RECONCILIATION"} <= {e["kind"] for e in staff.json()}
    assert "raw_payload" not in mine.text and "payment_intent" not in mine.text
    assert client.get(f"{V2}/payments/{payment['id']}/history", headers=other).status_code == 404
    assert client.get(f"{V2}/payments/99999/history", headers=h(FINANCE)).status_code == 404
    assert client.get(f"{V2}/payments/{payment['id']}/history", headers=h(UserRole.SELLER)).status_code == 403


# ------------------------------------------------------------------ settlements
def settlement_body(**over):
    now = datetime.now(timezone.utc)
    body = {"provider": "stripe", "provider_reference": "po_test_001", "currency": "inr",
            "period_start": (now - timedelta(days=1)).isoformat(), "period_end": (now + timedelta(days=1)).isoformat(),
            "reported_gross": "0.00", "reported_refunds": "0.00", "reported_fees": "0.00"}
    body.update(over)
    return body


def test_settlement_matches_ledger_or_raises_variance(client, db, h, catalog, stripe_mock):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    pay_order(client, h(CUSTOMER), order)
    total = Decimal(str(order["grand_total"]))

    ok = client.post(f"{V2}/finance/settlements", headers=h(FINANCE),
                     json=settlement_body(reported_gross=str(total), reported_fees="10.00"))
    assert ok.status_code == 201, ok.text
    assert ok.json()["status"] == "MATCHED" and Decimal(ok.json()["variance"]) == 0
    assert Decimal(ok.json()["reported_net"]) == total - 10 and Decimal(ok.json()["expected_gross"]) == total

    again = client.post(f"{V2}/finance/settlements", headers=h(FINANCE),
                        json=settlement_body(reported_gross=str(total)))
    assert again.status_code == 409                                              # same statement recorded twice

    off = client.post(f"{V2}/finance/settlements", headers=h(FINANCE),
                      json=settlement_body(provider_reference="po_test_002", reported_gross=str(total + 50)))
    assert off.status_code == 201 and off.json()["status"] == "VARIANCE" and Decimal(off.json()["variance"]) == 50
    queue = client.get(f"{V2}/finance/exceptions?exc_type=SETTLEMENT_VARIANCE", headers=h(FINANCE)).json()
    assert queue["total"] == 1 and queue["items"][0]["settlement_id"] == off.json()["id"]

    assert client.get(f"{V2}/finance/settlements?status=VARIANCE", headers=h(FINANCE)).json()["total"] == 1
    assert client.get(f"{V2}/finance/settlements", headers=h(FINANCE)).json()["total"] == 2
    assert client.get(f"{V2}/finance/settlements/{ok.json()['id']}", headers=h(FINANCE)).status_code == 200
    assert client.get(f"{V2}/finance/settlements/99999", headers=h(FINANCE)).status_code == 404


def test_settlement_counts_successful_refunds(client, db, h, catalog, stripe_mock):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    payment = pay_order(client, h(CUSTOMER), order)
    refund = client.post(f"{V2}/refunds", headers=h(CUSTOMER),
                         json={"payment_id": payment["id"], "amount": "100.00", "reason": "Damaged item"}).json()
    client.patch(f"/refunds/{refund['id']}/decision", json={"status": "APPROVED"}, headers=h(FINANCE))
    client.post(f"/refunds/{refund['id']}/process", headers=h(FINANCE))
    total = Decimal(str(order["grand_total"]))
    result = client.post(f"{V2}/finance/settlements", headers=h(FINANCE),
                         json=settlement_body(reported_gross=str(total), reported_refunds="100.00"))
    assert result.json()["status"] == "MATCHED" and Decimal(result.json()["expected_refunds"]) == Decimal("100.00")


@pytest.mark.parametrize("override", [
    {"period_end": "2000-01-01T00:00:00"},                      # ends before it starts
    {"reported_gross": "-1"}, {"reported_fees": "-0.01"},
    {"reported_gross": "10", "reported_fees": "11"},             # fees above gross
    {"currency": "RUPEE"}, {"provider_reference": "x"}, {"reported_gross": "1.234"},
])
def test_settlement_validation(client, h, override):
    assert client.post(f"{V2}/finance/settlements", headers=h(FINANCE), json=settlement_body(**override)).status_code == 422
