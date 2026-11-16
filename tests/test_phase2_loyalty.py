"""Loyalty points: earning, redemption at checkout, FIFO lots, expiry, reversals, admin APIs."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal


from app.core.config import settings
from app.models import CustomerSegment, CustomerSegmentMember, LoyaltyLedger, LoyaltyRule, Order, UserRole
from app.services import loyalty
from app.services.reservations import now_naive

from tests.conftest import checkout_payload, pay_order, place_order

CUSTOMER = UserRole.CUSTOMER
ADMIN = UserRole.ADMIN
FINANCE = UserRole.FINANCE_MANAGER
SUPPORT = UserRole.SUPPORT_AGENT
ORDER_MGR = UserRole.ORDER_MANAGER
D = Decimal
V2 = "/v2/loyalty"


def add_rule(client, h, **over):
    body = {"name": "Base", "earn_rate": "1"}
    body.update(over)
    r = client.post(f"{V2}/rules", json=body, headers=h(ADMIN))
    assert r.status_code == 201, r.text
    return r.json()


def give_points(client, h, users, points=1000):
    r = client.post(f"{V2}/users/{users[CUSTOMER].id}/adjust", json={"points": points, "reason": "welcome bonus"}, headers=h(ADMIN))
    assert r.status_code == 201, r.text
    return r.json()


def me(client, h):
    return client.get(f"{V2}/me", headers=h(CUSTOMER)).json()


def deliver(client, h, order_id):
    for status in ("PROCESSING", "SHIPPED", "DELIVERED"):
        r = client.patch(f"/orders/{order_id}/status", json={"status": status}, headers=h(ORDER_MGR))
        assert r.status_code == 200, r.text


def delivered_paid_order(client, h, catalog, stripe_mock, quantity=2):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id, quantity=quantity)
    payment = pay_order(client, h(CUSTOMER), order)
    deliver(client, h, order["id"])
    return order, payment


def checkout_with_points(client, h, catalog, points, quantity=2):
    r = client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": quantity}, headers=h(CUSTOMER))
    assert r.status_code == 200, r.text
    return client.post("/checkout", json=checkout_payload(catalog.shipping_id, redeem_points=points), headers=h(CUSTOMER))


def refund_fully_in_steps(client, h, payment_id, amounts):
    for amount in amounts:
        req = client.post("/v2/refunds", headers=h(CUSTOMER),
                          json={"payment_id": payment_id, "amount": str(amount), "reason": "Returned the goods"})
        assert req.status_code == 201, req.text
        rid = req.json()["id"]
        assert client.patch(f"/refunds/{rid}/decision", json={"status": "APPROVED"}, headers=h(FINANCE)).status_code == 200
        assert client.post(f"/refunds/{rid}/process", headers=h(FINANCE)).status_code == 200


# ------------------------------------------------------------------ earning
def test_points_are_credited_once_when_a_paid_order_is_delivered(client, db, h, catalog, stripe_mock):
    add_rule(client, h, earn_rate="1")
    order, _ = delivered_paid_order(client, h, catalog, stripe_mock)
    account = me(client, h)
    assert account["balance"] == 1000 and account["available_points"] == 1000 and account["lifetime_earned"] == 1000
    entries = client.get(f"{V2}/me/transactions", headers=h(CUSTOMER)).json()
    assert entries["total"] == 1 and entries["items"][0]["entry_type"] == "EARN" and entries["items"][0]["order_id"] == order["id"]
    assert entries["items"][0]["expires_at"] is not None

    again = client.post(f"{V2}/orders/{order['id']}/credit", headers=h(ADMIN))
    assert again.status_code == 200 and again.json() == {"order_id": order["id"], "points_credited": 0, "already_credited": True}
    assert me(client, h)["balance"] == 1000


def test_no_points_without_a_rule_or_before_delivery_or_without_payment(client, db, h, catalog, stripe_mock):
    unpaid = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id, quantity=1)
    assert client.post(f"{V2}/orders/{unpaid['id']}/credit", headers=h(ADMIN)).status_code == 409   # not delivered
    delivered_paid_order(client, h, catalog, stripe_mock, quantity=1)                                  # no rule exists
    assert me(client, h)["balance"] == 0
    assert client.post(f"{V2}/orders/99999/credit", headers=h(ADMIN)).status_code == 404
    assert client.post(f"{V2}/orders/{unpaid['id']}/credit", headers=h(CUSTOMER)).status_code == 403


def test_manual_credit_catches_up_when_the_rule_was_added_late(client, db, h, catalog, stripe_mock):
    order, _ = delivered_paid_order(client, h, catalog, stripe_mock)
    assert me(client, h)["balance"] == 0
    add_rule(client, h, earn_rate="0.5")
    credited = client.post(f"{V2}/orders/{order['id']}/credit", headers=h(FINANCE))
    assert credited.status_code == 200 and credited.json()["points_credited"] == 500 and credited.json()["already_credited"] is False


def test_split_shipment_delivery_also_credits_points(client, db, h, catalog, stripe_mock):
    add_rule(client, h, earn_rate="1")
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id, quantity=2)
    pay_order(client, h(CUSTOMER), order)
    item = client.get(f"/orders/{order['id']}/items", headers=h(CUSTOMER)).json()[0]["id"]
    ship = client.post(f"/v2/orders/{order['id']}/shipments", headers=h(UserRole.DELIVERY_MANAGER),
                       json={"carrier_name": "BlueDart", "items": [{"order_item_id": item, "quantity": 2}]}).json()
    assert me(client, h)["balance"] == 0                                          # shipped is not delivered
    client.post(f"/v2/orders/{order['id']}/shipments/{ship['id']}/deliver", headers=h(UserRole.DELIVERY_MANAGER))
    assert me(client, h)["balance"] == 1000


def test_rule_selection_priority_category_minimum_segment_and_cap(db, users, catalog):
    uid, cat = users[CUSTOMER].id, catalog.category_id
    lines = [(cat, D("1000.00"))]
    assert loyalty.compute_points(db, uid, lines) == (0, None)

    base = LoyaltyRule(name="Base", earn_rate=D("1"), priority=1)
    db.add(base)
    db.commit()
    assert loyalty.compute_points(db, uid, lines)[0] == 1000

    boosted = LoyaltyRule(name="Electronics", earn_rate=D("2"), priority=5, category_id=cat, max_points_per_order=500)
    db.add(boosted)
    db.commit()
    assert loyalty.compute_points(db, uid, lines)[0] == 500                      # category rule wins, capped
    assert loyalty.compute_points(db, uid, [(999, D("1000.00"))])[0] == 1000     # other category: base rule

    db.add(LoyaltyRule(name="Big spender", earn_rate=D("3"), priority=9, min_order_value=D("5000")))
    db.commit()
    assert loyalty.compute_points(db, uid, lines)[0] == 500                      # minimum not reached: ignored

    segment = CustomerSegment(code="PLUS", name="Plus")
    db.add(segment)
    db.commit()
    db.add(LoyaltyRule(name="Plus members", earn_rate=D("4"), priority=10, segment_id=segment.id))
    db.commit()
    assert loyalty.compute_points(db, uid, lines)[0] == 500                      # not a member
    db.add(CustomerSegmentMember(segment_id=segment.id, user_id=uid))
    db.commit()
    assert loyalty.compute_points(db, uid, lines)[0] == 4000

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for rule in db.query(LoyaltyRule).all():
        rule.is_active = False
    db.add(LoyaltyRule(name="Ended", earn_rate=D("9"), priority=99, ends_at=now - timedelta(days=1)))
    db.add(LoyaltyRule(name="Future", earn_rate=D("9"), priority=99, starts_at=now + timedelta(days=1)))
    db.commit()
    assert loyalty.compute_points(db, uid, lines) == (0, None)                   # inactive / outside window


# ------------------------------------------------------------------ redemption
def test_breakdown_previews_points_redemption(client, db, h, users, catalog):
    add_rule(client, h, earn_rate="1")
    give_points(client, h, users, 1000)
    client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": 2}, headers=h(CUSTOMER))
    params = {"shipping_method_id": catalog.shipping_id}
    plain = client.get("/v2/pricing/breakdown", params=params, headers=h(CUSTOMER)).json()
    assert plain["loyalty"]["available_points"] == 1000 and plain["loyalty"]["max_redeemable_points"] == 1000
    assert plain["loyalty"]["estimated_points_to_earn"] == 1000 and D(plain["grand_total"]) == D("1230.00")

    spent = client.get("/v2/pricing/breakdown", params={**params, "redeem_points": 200}, headers=h(CUSTOMER)).json()
    assert spent["loyalty"]["points_redeemed"] == 200 and D(spent["loyalty"]["discount"]) == D("50.00")
    assert D(spent["lines"][0]["loyalty_share"]) == D("50.00") and D(spent["lines"][0]["line_total"]) == D("1130.00")
    assert D(spent["discount_total"]) == D("50.00") and D(spent["grand_total"]) == D("1180.00")
    assert spent["loyalty"]["estimated_points_to_earn"] == 950                   # earned on what was actually paid


def test_redeeming_points_at_checkout(client, db, h, users, catalog):
    give_points(client, h, users, 1000)
    placed = checkout_with_points(client, h, catalog, 200)
    assert placed.status_code == 201, placed.text
    order = placed.json()
    assert D(order["grand_total"]) == D("1180.00") and D(order["discount_total"]) == D("50.00")

    db.expire_all()
    row = db.get(Order, order["id"])
    assert row.loyalty_points_redeemed == 200 and D(str(row.loyalty_discount)) == D("50.00")
    item = client.get(f"/orders/{order['id']}/items", headers=h(CUSTOMER)).json()[0]
    assert D(item["discount_amount"]) == D("50.00") and D(item["line_total"]) == D("1130.00")
    account = me(client, h)
    assert account["balance"] == 800 and account["available_points"] == 800
    spent = client.get(f"{V2}/me/transactions?entry_type=REDEEM", headers=h(CUSTOMER)).json()
    assert spent["total"] == 1 and spent["items"][0]["points"] == -200 and spent["items"][0]["order_id"] == order["id"]


def test_redemption_validation_and_caps(client, db, h, users, catalog, monkeypatch):
    give_points(client, h, users, 1000)
    client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": 2}, headers=h(CUSTOMER))
    params = {"shipping_method_id": catalog.shipping_id}
    below = client.get("/v2/pricing/breakdown", params={**params, "redeem_points": 49}, headers=h(CUSTOMER))
    assert below.status_code == 400 and "Minimum redemption" in below.json()["detail"]
    too_many = client.get("/v2/pricing/breakdown", params={**params, "redeem_points": 5000}, headers=h(CUSTOMER))
    assert too_many.status_code == 409 and "Insufficient" in too_many.json()["detail"]

    monkeypatch.setattr(settings, "loyalty_max_redeem_percent", 10)             # at most 118.00 of 1180.00
    capped = client.get("/v2/pricing/breakdown", params={**params, "redeem_points": 500}, headers=h(CUSTOMER))
    assert capped.status_code == 409 and "At most 472 points" in capped.json()["detail"]
    ok = client.get("/v2/pricing/breakdown", params={**params, "redeem_points": 472}, headers=h(CUSTOMER))
    assert ok.status_code == 200 and D(ok.json()["loyalty"]["discount"]) == D("118.00")
    assert ok.json()["loyalty"]["max_redeemable_points"] == 472


def test_failed_redemption_rolls_the_whole_checkout_back(client, db, h, users, catalog):
    give_points(client, h, users, 100)
    failed = checkout_with_points(client, h, catalog, 5000)
    assert failed.status_code == 409
    assert me(client, h)["balance"] == 100
    cart = client.get("/cart", headers=h(CUSTOMER)).json()
    assert sum(i["quantity"] for i in cart["items"]) == 2                         # cart untouched


def test_redeemed_points_come_back_when_the_order_is_cancelled(client, db, h, users, catalog):
    give_points(client, h, users, 1000)
    order = checkout_with_points(client, h, catalog, 200).json()
    assert me(client, h)["available_points"] == 800
    cancelled = client.post(f"/orders/{order['id']}/cancel", json={"reason": "changed my mind"}, headers=h(CUSTOMER))
    assert cancelled.status_code == 200, cancelled.text
    account = me(client, h)
    assert account["balance"] == 1000 and account["available_points"] == 1000
    types = [e["entry_type"] for e in client.get(f"{V2}/me/transactions", headers=h(CUSTOMER)).json()["items"]]
    assert types == ["REVERSE_REDEEM", "REDEEM", "ADJUST"]


def test_item_level_full_cancellation_also_returns_points(client, db, h, users, catalog):
    give_points(client, h, users, 1000)
    order = checkout_with_points(client, h, catalog, 200).json()
    item = client.get(f"/orders/{order['id']}/items", headers=h(CUSTOMER)).json()[0]["id"]
    done = client.post(f"/v2/orders/{order['id']}/cancel-items", headers=h(CUSTOMER),
                       json={"reason": "no longer needed", "items": [{"order_item_id": item, "quantity": 2}]})
    assert done.status_code == 200 and done.json()["order_status"] == "CANCELLED"
    assert me(client, h)["available_points"] == 1000


def test_points_are_spent_from_the_lot_that_expires_first(client, db, h, users, catalog):
    older = give_points(client, h, users, 100)       # created first ...
    newer = give_points(client, h, users, 100)
    db.expire_all()
    # ... but it expires LATER, so expiry date (not creation order) must decide what is spent first
    db.get(LoyaltyLedger, older["id"]).expires_at = now_naive() + timedelta(days=20)
    db.get(LoyaltyLedger, newer["id"]).expires_at = now_naive() + timedelta(days=10)
    db.commit()
    assert checkout_with_points(client, h, catalog, 150).status_code == 201
    db.expire_all()
    assert db.get(LoyaltyLedger, newer["id"]).points_remaining == 0
    assert db.get(LoyaltyLedger, older["id"]).points_remaining == 50
    assert me(client, h)["expiring_within_30_days"] == 50


# ------------------------------------------------------------------ expiry
def test_expired_points_are_removed_and_cannot_be_spent(client, db, h, users, catalog):
    give_points(client, h, users, 300)
    db.expire_all()
    lot = db.query(LoyaltyLedger).filter_by(entry_type="ADJUST").one()
    lot.expires_at = now_naive() - timedelta(hours=1)
    db.commit()

    client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": 1}, headers=h(CUSTOMER))
    blocked = client.get("/v2/pricing/breakdown", params={"redeem_points": 100, "shipping_method_id": catalog.shipping_id},
                         headers=h(CUSTOMER))
    assert blocked.status_code == 409                                              # lazily expired first
    account = me(client, h)
    assert account["balance"] == 0 and account["available_points"] == 0
    kinds = [e["entry_type"] for e in client.get(f"{V2}/me/transactions", headers=h(CUSTOMER)).json()["items"]]
    assert kinds == ["EXPIRE", "ADJUST"]


def test_expiry_run_is_admin_only_and_idempotent(client, db, h, users):
    give_points(client, h, users, 100)
    db.expire_all()
    db.query(LoyaltyLedger).filter_by(entry_type="ADJUST").one().expires_at = now_naive() - timedelta(minutes=5)
    db.commit()
    assert client.post(f"{V2}/expire", headers=h(CUSTOMER)).status_code == 403
    assert client.post(f"{V2}/expire", headers=h(FINANCE)).status_code == 403
    assert client.post(f"{V2}/expire", headers=h(ADMIN)).json() == {"expired_lots": 1}
    assert client.post(f"{V2}/expire", headers=h(ADMIN)).json() == {"expired_lots": 0}
    assert db.query(LoyaltyLedger).filter_by(entry_type="EXPIRE").count() == 1
    assert me(client, h)["balance"] == 0


# ------------------------------------------------------------------ reversals on refund
def test_earned_points_are_reversed_in_proportion_to_refunds(client, db, h, catalog, stripe_mock):
    add_rule(client, h, earn_rate="1")
    order, payment = delivered_paid_order(client, h, catalog, stripe_mock)
    assert me(client, h)["balance"] == 1000
    refund_fully_in_steps(client, h, payment["id"], ["615.00"])                    # half of 1230.00
    assert me(client, h)["balance"] == 500
    refund_fully_in_steps(client, h, payment["id"], ["615.00"])
    assert me(client, h)["balance"] == 0
    reversals = client.get(f"{V2}/me/transactions?entry_type=REVERSE_EARN", headers=h(CUSTOMER)).json()
    assert reversals["total"] == 2 and [e["points"] for e in reversals["items"]] == [-500, -500]


def test_reversal_only_takes_back_unspent_points(client, db, h, users, catalog, stripe_mock):
    add_rule(client, h, earn_rate="1")
    order, payment = delivered_paid_order(client, h, catalog, stripe_mock)          # +1000 points
    assert checkout_with_points(client, h, catalog, 800).status_code == 201         # spend 800 of them
    refund_fully_in_steps(client, h, payment["id"], ["1230.00"])                    # full refund of order 1
    account = me(client, h)
    assert account["balance"] == 0 and account["available_points"] == 0             # never negative
    entry = client.get(f"{V2}/me/transactions?entry_type=REVERSE_EARN", headers=h(CUSTOMER)).json()["items"][0]
    assert entry["points"] == -200


# ------------------------------------------------------------------ account + admin APIs
def test_adjustments(client, db, h, users):
    uid = users[CUSTOMER].id
    url = f"{V2}/users/{uid}/adjust"
    assert client.post(url, json={"points": 0, "reason": "none"}, headers=h(ADMIN)).status_code == 422
    assert client.post(url, json={"points": 10}, headers=h(ADMIN)).status_code == 422                 # reason required
    assert client.post(url, json={"points": 2_000_000, "reason": "too much"}, headers=h(ADMIN)).status_code == 422
    assert client.post(url, json={"points": -10, "reason": "no balance yet"}, headers=h(ADMIN)).status_code == 409
    assert client.post(url, json={"points": 10, "reason": "x"}, headers=h(ADMIN)).status_code == 422
    assert client.post(f"{V2}/users/99999/adjust", json={"points": 10, "reason": "ghost user"}, headers=h(ADMIN)).status_code == 404
    assert client.post(f"{V2}/users/{users[ADMIN].id}/adjust", json={"points": 10, "reason": "not a customer"},
                       headers=h(ADMIN)).status_code == 404
    for role in (CUSTOMER, SUPPORT, FINANCE, UserRole.SELLER):
        assert client.post(url, json={"points": 10, "reason": "unauthorised"}, headers=h(role)).status_code == 403

    key = {**h(ADMIN), "Idempotency-Key": "adjust-key-0001"}
    one = client.post(url, json={"points": 100, "reason": "goodwill gesture"}, headers=key)
    two = client.post(url, json={"points": 100, "reason": "goodwill gesture"}, headers=key)
    assert one.status_code == 201 and two.json()["id"] == one.json()["id"]
    assert me(client, h)["balance"] == 100
    assert client.post(url, json={"points": -30, "reason": "correction"}, headers=h(ADMIN)).status_code == 201
    assert me(client, h)["available_points"] == 70
    assert client.post(url, json={"points": -71, "reason": "too much"}, headers=h(ADMIN)).status_code == 409


def test_account_and_history_access(client, db, h, users, second_customer):
    _, other = second_customer
    give_points(client, h, users, 100)
    uid = users[CUSTOMER].id
    assert client.get(f"{V2}/users/{uid}", headers=h(SUPPORT)).json()["balance"] == 100
    assert client.get(f"{V2}/users/{uid}/transactions", headers=h(FINANCE)).json()["total"] == 1
    assert client.get(f"{V2}/users/{uid}", headers=h(CUSTOMER)).status_code == 403
    assert client.get(f"{V2}/users/{uid}", headers=h(UserRole.SELLER)).status_code == 403
    assert client.get(f"{V2}/users/99999", headers=h(ADMIN)).status_code == 404
    assert client.get(f"{V2}/me", headers=h(ADMIN)).status_code == 403
    assert client.get(f"{V2}/me", headers=h(UserRole.SELLER)).status_code == 403
    assert client.get(f"{V2}/me", headers=other).json()["balance"] == 0                 # sees only their own
    assert client.get(f"{V2}/me/transactions?entry_type=BAD", headers=h(CUSTOMER)).status_code == 422
    assert client.get(f"{V2}/me/transactions?page_size=101", headers=h(CUSTOMER)).status_code == 422
    assert client.get(f"{V2}/me/transactions?entry_type=EARN", headers=h(CUSTOMER)).json()["total"] == 0


def test_earning_rule_management(client, db, h, catalog):
    url = f"{V2}/rules"
    now = datetime.now(timezone.utc)
    bad = [{"name": "x", "earn_rate": "1"}, {"name": "Zero", "earn_rate": "0"}, {"name": "Neg", "earn_rate": "-1"},
           {"name": "Many", "earn_rate": "1.00001"}, {"name": "Rate", "earn_rate": "5000"},
           {"name": "Prio", "earn_rate": "1", "priority": 0}, {"name": "Min", "earn_rate": "1", "min_order_value": "-1"},
           {"name": "Window", "earn_rate": "1", "starts_at": now.isoformat(), "ends_at": (now - timedelta(days=1)).isoformat()},
           {"name": "Expiry", "earn_rate": "1", "expiry_days": -1}]
    for body in bad:
        assert client.post(url, json=body, headers=h(ADMIN)).status_code == 422, body
    assert client.post(url, json={"name": "Cat", "earn_rate": "1", "category_id": 9999}, headers=h(ADMIN)).status_code == 404
    assert client.post(url, json={"name": "Seg", "earn_rate": "1", "segment_id": 9999}, headers=h(ADMIN)).status_code == 404
    assert client.post(url, json={"name": "Okay", "earn_rate": "1"}, headers=h(FINANCE)).status_code == 403

    rule = add_rule(client, h, name="Electronics", earn_rate="2.5", category_id=catalog.category_id, max_points_per_order=300)
    assert D(rule["earn_rate"]) == D("2.5") and rule["is_active"] is True
    rid = rule["id"]
    assert client.get(f"{url}/{rid}", headers=h(FINANCE)).status_code == 200
    assert client.get(f"{url}/99999", headers=h(ADMIN)).status_code == 404
    assert client.get(url, headers=h(SUPPORT)).status_code == 403
    assert client.patch(f"{url}/{rid}", json={}, headers=h(ADMIN)).status_code == 422
    assert client.patch(f"{url}/{rid}", json={"name": None}, headers=h(ADMIN)).status_code == 422
    assert client.patch(f"{url}/{rid}", json={"earn_rate": "3"}, headers=h(FINANCE)).status_code == 403
    changed = client.patch(f"{url}/{rid}", json={"earn_rate": "3", "max_points_per_order": None, "expiry_days": 0}, headers=h(ADMIN))
    assert changed.status_code == 200 and D(changed.json()["earn_rate"]) == D("3") and changed.json()["max_points_per_order"] is None
    off = client.delete(f"{url}/{rid}", headers=h(ADMIN))
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert client.get(f"{url}?is_active=true", headers=h(ADMIN)).json()["total"] == 0
    assert client.get(f"{url}?is_active=false", headers=h(FINANCE)).json()["total"] == 1


def test_expiry_days_zero_means_points_never_expire(client, db, h, catalog, stripe_mock):
    add_rule(client, h, earn_rate="1", expiry_days=0)
    delivered_paid_order(client, h, catalog, stripe_mock)
    entry = client.get(f"{V2}/me/transactions", headers=h(CUSTOMER)).json()["items"][0]
    assert entry["expires_at"] is None and me(client, h)["expiring_within_30_days"] == 0
