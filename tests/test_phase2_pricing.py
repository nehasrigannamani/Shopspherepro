"""Promotion rules (priority, stacking, caps, segments, limits), coupons, redemptions, price breakdown."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import Coupon, CustomerSegment, Promotion, PromotionRedemption, UserRole

from tests.conftest import place_order

CUSTOMER = UserRole.CUSTOMER
ADMIN = UserRole.ADMIN
PM = UserRole.PRODUCT_MANAGER
D = Decimal


def make_promo(db, **kw):
    now = datetime.now(timezone.utc)
    data = dict(name="Promo", discount_type="PERCENTAGE", discount_value=D("10.00"), target_type="ALL",
                starts_at=now - timedelta(days=1), expires_at=now + timedelta(days=30), priority=1, stackable=False)
    data.update(kw)
    row = Promotion(**data)
    db.add(row)
    db.commit()
    return row.id


def add_to_cart(client, headers, product_id, quantity=2):
    r = client.post("/cart/items", json={"product_id": product_id, "quantity": quantity}, headers=headers)
    assert r.status_code == 200, r.text


def breakdown(client, h, catalog, **params):
    params.setdefault("shipping_method_id", catalog.shipping_id)
    return client.get("/v2/pricing/breakdown", params=params, headers=h(CUSTOMER))


def promo_discount(client, h, catalog, **params):
    r = breakdown(client, h, catalog, **params)
    assert r.status_code == 200, r.text
    return D(r.json()["promotion_discount"]), r.json()


# ------------------------------------------------------------------ selection rules
def test_breakdown_without_promotions_matches_cart_summary(client, h, catalog):
    add_to_cart(client, h(CUSTOMER), catalog.product_id)
    body = breakdown(client, h, catalog).json()
    summary = client.get("/cart/summary", params={"shipping_method_id": catalog.shipping_id}, headers=h(CUSTOMER)).json()
    assert D(body["subtotal"]) == D("1000.00") and D(body["tax_total"]) == D("180.00")
    assert D(body["grand_total"]) == D(summary["grand_total"]) == D("1230.00")
    assert body["promotions"] == [] and body["coupon"] is None and body["loyalty"]["points_redeemed"] == 0
    line = body["lines"][0]
    assert D(line["line_total"]) == D("1180.00") and line["promotions"] == []


def test_best_exclusive_promotion_wins_at_equal_priority(client, db, h, catalog):
    make_promo(db, name="Ten", discount_value=D("10"))
    twenty = make_promo(db, name="Twenty", discount_value=D("20"))
    add_to_cart(client, h(CUSTOMER), catalog.product_id)
    discount, body = promo_discount(client, h, catalog)
    assert discount == D("200.00") and [p["promotion_id"] for p in body["promotions"]] == [twenty]
    assert D(body["grand_total"]) == D("1000") - D("200") + D("144") + D("50")       # tax on 800


def test_priority_beats_a_bigger_discount(client, db, h, catalog):
    high = make_promo(db, name="Priority", discount_value=D("10"), priority=5)
    make_promo(db, name="Big", discount_value=D("30"), priority=1)
    add_to_cart(client, h(CUSTOMER), catalog.product_id)
    discount, body = promo_discount(client, h, catalog)
    assert discount == D("100.00") and body["promotions"][0]["promotion_id"] == high


def test_stackable_promotions_combine_unless_an_exclusive_one_is_better(client, db, h, catalog):
    make_promo(db, name="S10", discount_value=D("10"), stackable=True, priority=3)
    make_promo(db, name="S5", discount_value=D("5"), stackable=True, priority=2)
    exclusive = make_promo(db, name="E12", discount_value=D("12"))
    add_to_cart(client, h(CUSTOMER), catalog.product_id)
    discount, body = promo_discount(client, h, catalog)
    assert discount == D("150.00") and len(body["promotions"]) == 2          # stack (150) beats exclusive (120)

    db.get(Promotion, exclusive).discount_value = D("20")
    db.commit()
    discount, body = promo_discount(client, h, catalog)
    assert discount == D("200.00") and [p["promotion_id"] for p in body["promotions"]] == [exclusive]


def test_stacking_never_discounts_more_than_the_line_is_worth(client, db, h, catalog):
    make_promo(db, name="S60", discount_value=D("60"), stackable=True)
    make_promo(db, name="S60b", discount_value=D("60"), stackable=True)
    add_to_cart(client, h(CUSTOMER), catalog.product_id)
    discount, body = promo_discount(client, h, catalog)
    assert discount == D("1000.00") and D(body["lines"][0]["line_total"]) == D("0.00")


def test_minimum_cart_value_boundary(client, db, h, catalog):
    promo = make_promo(db, min_cart_value=D("1000.00"))
    add_to_cart(client, h(CUSTOMER), catalog.product_id)
    assert promo_discount(client, h, catalog)[0] == D("100.00")                   # exactly the minimum
    db.get(Promotion, promo).min_cart_value = D("1000.01")
    db.commit()
    assert promo_discount(client, h, catalog)[0] == D("0.00")


def test_maximum_discount_caps_the_promotion(client, db, h, catalog):
    make_promo(db, discount_value=D("50"), max_discount=D("100.00"))
    add_to_cart(client, h(CUSTOMER), catalog.product_id)
    discount, body = promo_discount(client, h, catalog)
    assert discount == D("100.00") and D(body["promotions"][0]["discount"]) == D("100.00")


def test_cap_is_shared_across_lines(client, db, h, catalog, variant_catalog):
    make_promo(db, discount_value=D("50"), max_discount=D("300.00"))
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 2)                       # 1000
    r = client.post("/cart/items", json={"product_id": variant_catalog.product_id,
                                         "variant_id": variant_catalog.medium_id, "quantity": 1}, headers=h(CUSTOMER))
    assert r.status_code == 200, r.text                                           # 800
    discount, body = promo_discount(client, h, catalog)
    assert discount == D("300.00")                                                # 50% of 1800 = 900, capped
    assert sum(D(l["promotion_discount"]) for l in body["lines"]) == D("300.00")


def test_segment_promotion_only_for_members(client, db, h, catalog, users):
    segment = CustomerSegment(code="VIP", name="VIP customers")
    db.add(segment)
    db.commit()
    make_promo(db, discount_value=D("15"), customer_segment_id=segment.id)
    add_to_cart(client, h(CUSTOMER), catalog.product_id)
    assert promo_discount(client, h, catalog)[0] == D("0.00")
    added = client.post(f"/v2/segments/{segment.id}/members", json={"user_id": users[CUSTOMER].id}, headers=h(ADMIN))
    assert added.status_code == 201
    assert promo_discount(client, h, catalog)[0] == D("150.00")


def test_promotion_can_exclude_coupon_use(client, db, h, catalog, coupon):
    make_promo(db, allow_with_coupon=False)
    add_to_cart(client, h(CUSTOMER), catalog.product_id)
    assert promo_discount(client, h, catalog)[0] == D("100.00")
    discount, body = promo_discount(client, h, catalog, coupon_code="SAVE10")
    assert discount == D("0.00") and D(body["coupon"]["discount"]) == D("100.00")


def test_per_customer_promotion_limit(client, db, h, catalog):
    promo = make_promo(db, per_customer_limit=1)
    place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id, quantity=1)
    assert db.query(PromotionRedemption).filter_by(promotion_id=promo, status="APPLIED").count() == 1
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 1)
    assert promo_discount(client, h, catalog)[0] == D("0.00")                     # used up for this customer


def test_usage_limit_counts_once_per_order_even_with_several_lines(client, db, h, catalog, variant_catalog):
    promo = make_promo(db, usage_limit=5)
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 1)
    client.post("/cart/items", json={"product_id": variant_catalog.product_id,
                                     "variant_id": variant_catalog.medium_id, "quantity": 1}, headers=h(CUSTOMER))
    order = client.post("/checkout", headers=h(CUSTOMER), json={
        "shipping_method_id": catalog.shipping_id, "shipping_full_name": "Chris Customer", "shipping_phone": "+919000000001",
        "shipping_address_line1": "12 MG Road", "shipping_city": "Bengaluru", "shipping_state": "Karnataka",
        "shipping_postal_code": "560001"})
    assert order.status_code == 201, order.text
    db.expire_all()
    assert db.get(Promotion, promo).used_count == 1


# ------------------------------------------------------------------ coupons
def test_coupon_maximum_discount(client, db, h, catalog, coupon):
    add_to_cart(client, h(CUSTOMER), catalog.product_id)
    patch = client.patch(f"/v2/coupons/{coupon.id}/rules", json={"max_discount": "40.00"}, headers=h(ADMIN))
    assert patch.status_code == 200 and D(patch.json()["max_discount"]) == D("40.00")
    body = breakdown(client, h, catalog, coupon_code="SAVE10").json()
    assert D(body["coupon"]["discount"]) == D("40.00")


def test_coupon_per_customer_limit_blocks_reuse(client, db, h, catalog, coupon):
    assert client.patch(f"/v2/coupons/{coupon.id}/rules", json={"per_customer_limit": 1}, headers=h(ADMIN)).status_code == 200
    place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id, quantity=1, coupon_code="SAVE10")
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 1)
    again = breakdown(client, h, catalog, coupon_code="SAVE10")
    assert again.status_code == 400 and "maximum number of times" in again.json()["detail"]
    check = client.get("/v2/pricing/coupon-eligibility", params={"coupon_code": "save10"}, headers=h(CUSTOMER)).json()
    assert check["eligible"] is False and any("maximum number of times" in r for r in check["reasons"])


def test_coupon_restricted_to_a_segment(client, db, h, catalog, coupon, users):
    segment = CustomerSegment(code="GOLD", name="Gold")
    db.add(segment)
    db.commit()
    assert client.patch(f"/v2/coupons/{coupon.id}/rules", json={"customer_segment_id": segment.id},
                        headers=h(ADMIN)).status_code == 200
    add_to_cart(client, h(CUSTOMER), catalog.product_id)
    blocked = breakdown(client, h, catalog, coupon_code="SAVE10")
    assert blocked.status_code == 400 and "not available for your account" in blocked.json()["detail"]
    client.post(f"/v2/segments/{segment.id}/members", json={"user_id": users[CUSTOMER].id}, headers=h(ADMIN))
    assert breakdown(client, h, catalog, coupon_code="SAVE10").status_code == 200


def test_coupon_eligibility_endpoint(client, db, h, catalog, coupon):
    url = "/v2/pricing/coupon-eligibility"
    assert client.get(url, params={"coupon_code": "NOPE"}, headers=h(CUSTOMER)).json() == {
        "code": "NOPE", "eligible": False, "reasons": ["Coupon not found"], "min_order_amount": None,
        "max_discount": None, "estimated_discount": None}
    add_to_cart(client, h(CUSTOMER), catalog.product_id)
    ok = client.get(url, params={"coupon_code": "SAVE10"}, headers=h(CUSTOMER)).json()
    assert ok["eligible"] is True and D(ok["estimated_discount"]) == D("100.00") and ok["reasons"] == []

    coupon_row = db.get(Coupon, coupon.id)
    coupon_row.min_order_amount = D("5000.00")
    db.commit()
    low = client.get(url, params={"coupon_code": "SAVE10"}, headers=h(CUSTOMER)).json()
    assert low["eligible"] is False and "minimum subtotal" in low["reasons"][0]

    coupon_row.min_order_amount = D("0")
    coupon_row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    expired = client.get(url, params={"coupon_code": "SAVE10"}, headers=h(CUSTOMER)).json()
    assert expired["eligible"] is False and "validity period" in expired["reasons"][0]

    assert client.get(url, params={"coupon_code": "SAVE10"}, headers=h(UserRole.SUPPORT_AGENT)).status_code == 403
    assert client.get(url, params={"coupon_code": ""}, headers=h(CUSTOMER)).status_code == 422


# ------------------------------------------------------------------ redemption records
def test_redemptions_are_recorded_and_reversed_on_cancellation(client, db, h, catalog, coupon):
    promo = make_promo(db, discount_value=D("10"))
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id, quantity=2, coupon_code="SAVE10")
    # promotion 100 off, then coupon 10% of the remaining 900 = 90
    assert D(order["discount_total"]) == D("190.00")

    mine = client.get(f"/v2/promotions/{promo}/redemptions", headers=h(ADMIN)).json()
    assert mine["total"] == 1 and mine["items"][0]["status"] == "APPLIED" and D(mine["items"][0]["discount_amount"]) == D("100.00")
    assert client.get(f"/v2/coupons/{coupon.id}/redemptions", headers=h(ADMIN)).json()["total"] == 1
    db.expire_all()
    assert db.get(Promotion, promo).used_count == 1 and db.get(Coupon, coupon.id).used_count == 1

    cancelled = client.post(f"/orders/{order['id']}/cancel", json={"reason": "changed my mind"}, headers=h(CUSTOMER))
    assert cancelled.status_code == 200, cancelled.text
    db.expire_all()
    assert db.get(Promotion, promo).used_count == 0 and db.get(Coupon, coupon.id).used_count == 0
    reversed_ = client.get(f"/v2/promotions/{promo}/redemptions?status=REVERSED", headers=h(ADMIN)).json()
    assert reversed_["total"] == 1 and reversed_["items"][0]["reversed_at"]
    assert client.get(f"/v2/promotions/{promo}/redemptions?status=APPLIED", headers=h(ADMIN)).json()["total"] == 0
    assert client.get("/v2/promotions/99999/redemptions", headers=h(ADMIN)).status_code == 404


# ------------------------------------------------------------------ rules + segments admin APIs
def test_promotion_rules_endpoint(client, db, h, catalog):
    promo = make_promo(db)
    url = f"/v2/promotions/{promo}/rules"
    assert client.patch(url, json={}, headers=h(ADMIN)).status_code == 422
    assert client.patch(url, json={"min_cart_value": "-1"}, headers=h(ADMIN)).status_code == 422
    assert client.patch(url, json={"max_discount": "0"}, headers=h(ADMIN)).status_code == 422
    assert client.patch(url, json={"per_customer_limit": 0}, headers=h(ADMIN)).status_code == 422
    assert client.patch(url, json={"priority": None}, headers=h(ADMIN)).status_code == 422
    assert client.patch(url, json={"stackable": None}, headers=h(ADMIN)).status_code == 422
    assert client.patch(url, json={"customer_segment_id": 9999}, headers=h(ADMIN)).status_code == 404
    assert client.patch("/v2/promotions/99999/rules", json={"stackable": True}, headers=h(ADMIN)).status_code == 404
    assert client.patch(url, json={"stackable": True}, headers=h(CUSTOMER)).status_code == 403
    assert client.patch(url, json={"stackable": True}, headers=h(UserRole.SUPPORT_AGENT)).status_code == 403

    ok = client.patch(url, json={"stackable": True, "priority": 7, "min_cart_value": "250.00", "max_discount": "75.50"},
                      headers=h(PM))
    assert ok.status_code == 200 and ok.json()["stackable"] is True and ok.json()["priority"] == 7
    assert D(ok.json()["max_discount"]) == D("75.50")
    cleared = client.patch(url, json={"max_discount": None}, headers=h(PM))
    assert cleared.status_code == 200 and cleared.json()["max_discount"] is None and cleared.json()["stackable"] is True


def test_promotion_listing_filters_and_paging(client, db, h):
    now = datetime.now(timezone.utc)
    make_promo(db, name="Live")
    make_promo(db, name="Old", starts_at=now - timedelta(days=10), expires_at=now - timedelta(days=5))
    make_promo(db, name="Off", is_active=False)
    everything = client.get("/v2/promotions", headers=h(PM)).json()
    assert everything["total"] == 3
    assert client.get("/v2/promotions?valid_now=true&is_active=true", headers=h(PM)).json()["total"] == 1
    assert client.get("/v2/promotions?page_size=2", headers=h(PM)).json()["items"].__len__() == 2
    assert client.get("/v2/promotions?page_size=101", headers=h(PM)).status_code == 422
    assert client.get("/v2/promotions?target_type=BAD", headers=h(PM)).status_code == 422
    assert client.get("/v2/promotions", headers=h(CUSTOMER)).status_code == 403


def test_segments_api(client, db, h, users):
    created = client.post("/v2/segments", json={"code": "vip_1", "name": "VIP"}, headers=h(ADMIN))
    assert created.status_code == 201 and created.json()["code"] == "VIP_1"
    seg = created.json()["id"]
    assert client.post("/v2/segments", json={"code": "VIP_1", "name": "Again"}, headers=h(ADMIN)).status_code == 409
    assert client.post("/v2/segments", json={"code": "bad code!", "name": "X"}, headers=h(ADMIN)).status_code == 422
    assert client.post("/v2/segments", json={"code": "OK", "name": "X"}, headers=h(PM)).status_code == 403
    assert client.get("/v2/segments", headers=h(PM)).status_code == 200
    assert client.get("/v2/segments", headers=h(CUSTOMER)).status_code == 403

    member = {"user_id": users[CUSTOMER].id}
    assert client.post(f"/v2/segments/{seg}/members", json=member, headers=h(ADMIN)).status_code == 201
    assert client.post(f"/v2/segments/{seg}/members", json=member, headers=h(ADMIN)).status_code == 409
    assert client.post(f"/v2/segments/{seg}/members", json={"user_id": users[ADMIN].id}, headers=h(ADMIN)).status_code == 404
    assert client.post(f"/v2/segments/{seg}/members", json={"user_id": 99999}, headers=h(ADMIN)).status_code == 404
    assert client.post("/v2/segments/99999/members", json=member, headers=h(ADMIN)).status_code == 404
    assert client.get(f"/v2/segments/{seg}/members", headers=h(PM)).json()["total"] == 1
    assert client.delete(f"/v2/segments/{seg}/members/{users[CUSTOMER].id}", headers=h(ADMIN)).status_code == 204
    assert client.delete(f"/v2/segments/{seg}/members/{users[CUSTOMER].id}", headers=h(ADMIN)).status_code == 404


@pytest.mark.parametrize("role", [UserRole.ORDER_MANAGER, UserRole.SELLER, UserRole.FINANCE_MANAGER])
def test_promotion_admin_role_matrix(client, h, role):
    assert client.get("/v2/promotions", headers=h(role)).status_code == 403
    assert client.patch("/v2/promotions/1/rules", json={"stackable": True}, headers=h(role)).status_code == 403
    assert client.patch("/v2/coupons/1/rules", json={"max_discount": "1"}, headers=h(role)).status_code == 403


def test_breakdown_requires_a_customer_cart(client, h, catalog):
    assert client.get("/v2/pricing/breakdown", headers=h(CUSTOMER)).status_code == 409           # empty cart
    assert client.get("/v2/pricing/breakdown", headers=h(UserRole.ADMIN)).status_code == 403
    assert client.get("/v2/pricing/breakdown").status_code in (401, 403)
    assert client.get("/v2/pricing/breakdown", params={"redeem_points": -1}, headers=h(CUSTOMER)).status_code == 422
    assert client.get("/v2/pricing/breakdown", params={"country": "India"}, headers=h(CUSTOMER)).status_code == 422
