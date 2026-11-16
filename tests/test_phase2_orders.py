"""Advanced order management: split shipments, back-orders, item cancellation, timeline, admin updates."""
from decimal import Decimal

import pytest

from app.models import AuditLog, Inventory, Refund, UserRole
from app.services.pricing import money

from tests.conftest import pay_order, place_order

CUSTOMER = UserRole.CUSTOMER
ORDER_MGR = UserRole.ORDER_MANAGER
DELIVERY = UserRole.DELIVERY_MANAGER
ADMIN = UserRole.ADMIN
V2 = "/v2/orders"


def make_order(client, h, catalog, stripe_mock, quantity=4, paid=True):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id, quantity=quantity)
    if paid:
        pay_order(client, h(CUSTOMER), order)
    items = client.get(f"/orders/{order['id']}/items", headers=h(CUSTOMER)).json()
    return order, items[0]["id"], items[0]


def ship(client, h, order_id, item_id, qty, role=DELIVERY, **extra):
    body = {"carrier_name": "BlueDart", "tracking_number": "TRK1", "items": [{"order_item_id": item_id, "quantity": qty}]}
    body.update(extra)
    return client.post(f"{V2}/{order_id}/shipments", json=body, headers=h(role))


def status_of(client, h, order_id):
    return client.get(f"/orders/{order_id}", headers=h(CUSTOMER)).json()["status"]


def on_hand(db, product_id):
    db.expire_all()
    return db.query(Inventory).filter(Inventory.product_id == product_id).one().quantity_on_hand


# ------------------------------------------------------------------ fulfilment + shipments
def test_fulfilment_details_start_clean(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=4)
    body = client.get(f"{V2}/{order['id']}/fulfilment", headers=h(CUSTOMER)).json()
    assert body["status"] == "CONFIRMED"
    assert body["totals"] == {"ordered": 4, "cancelled": 0, "shipped": 0, "delivered": 0,
                              "backordered": 0, "remaining_to_ship": 4}
    assert body["items"][0]["shippable_now"] == 4 and body["shipments"] == []


def test_split_shipment_full_lifecycle(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=4)
    oid = order["id"]

    first = ship(client, h, oid, item_id, 1)
    assert first.status_code == 201, first.text
    assert first.json()["shipment_number"].endswith("-S1") and first.json()["status"] == "SHIPPED"
    assert status_of(client, h, oid) == "PARTIALLY_SHIPPED"

    second = ship(client, h, oid, item_id, 3)
    assert second.status_code == 201
    assert status_of(client, h, oid) == "SHIPPED"

    s1, s2 = first.json()["id"], second.json()["id"]
    assert client.post(f"{V2}/{oid}/shipments/{s1}/deliver", headers=h(DELIVERY)).status_code == 200
    assert status_of(client, h, oid) == "SHIPPED"        # one parcel still in transit
    assert client.post(f"{V2}/{oid}/shipments/{s2}/deliver", headers=h(DELIVERY)).status_code == 200
    assert status_of(client, h, oid) == "DELIVERED"

    history = [(e["previous_status"], e["new_status"])
               for e in client.get(f"/orders/{oid}/status-history", headers=h(CUSTOMER)).json()]
    assert ("CONFIRMED", "PROCESSING") in history and ("PROCESSING", "PARTIALLY_SHIPPED") in history
    assert history[-1] == ("SHIPPED", "DELIVERED")

    timeline = client.get(f"{V2}/{oid}/timeline", headers=h(ORDER_MGR)).json()
    kinds = [e["type"] for e in timeline]
    assert kinds.count("SHIPMENT_SHIPPED") == 2 and kinds.count("SHIPMENT_DELIVERED") == 2
    assert kinds.count("STATUS_CHANGE") >= 5


def test_cannot_ship_more_than_available_or_foreign_items(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=4)
    oid = order["id"]
    assert ship(client, h, oid, item_id, 5).status_code == 409
    assert ship(client, h, oid, item_id + 999, 1).status_code == 422
    assert ship(client, h, oid, item_id, 0).status_code == 422
    assert ship(client, h, oid, item_id, 3).status_code == 201
    assert ship(client, h, oid, item_id, 2).status_code == 409      # only 1 left
    # duplicate lines in one request are merged, not double counted
    dup = client.post(f"{V2}/{oid}/shipments", headers=h(DELIVERY), json={
        "carrier_name": "BlueDart",
        "items": [{"order_item_id": item_id, "quantity": 1}, {"order_item_id": item_id, "quantity": 1}]})
    assert dup.status_code == 409


def test_unconfirmed_order_cannot_ship(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, paid=False)
    assert status_of(client, h, order["id"]) == "PLACED"
    assert ship(client, h, order["id"], item_id, 1).status_code == 409


@pytest.mark.parametrize("role, expected", [
    (CUSTOMER, 403), (UserRole.SUPPORT_AGENT, 403), (UserRole.SELLER, 403),
    (UserRole.FINANCE_MANAGER, 403), (UserRole.INVENTORY_MANAGER, 403),
    (DELIVERY, 201), (ORDER_MGR, 201), (ADMIN, 201),
])
def test_shipping_role_matrix(client, h, catalog, stripe_mock, role, expected):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=2)
    assert ship(client, h, order["id"], item_id, 1, role=role).status_code == expected


def test_shipment_is_idempotent(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=4)
    oid = order["id"]
    headers = {**h(DELIVERY), "Idempotency-Key": "ship-key-0001"}
    body = {"carrier_name": "BlueDart", "items": [{"order_item_id": item_id, "quantity": 2}]}
    first = client.post(f"{V2}/{oid}/shipments", json=body, headers=headers)
    again = client.post(f"{V2}/{oid}/shipments", json=body, headers=headers)
    assert first.status_code == 201 and again.status_code == 201
    assert again.headers.get("Idempotent-Replay") == "true" and again.json()["id"] == first.json()["id"]
    fulfilment = client.get(f"{V2}/{oid}/fulfilment", headers=h(ORDER_MGR)).json()
    assert fulfilment["totals"]["shipped"] == 2 and len(fulfilment["shipments"]) == 1
    body["carrier_name"] = "Other"
    assert client.post(f"{V2}/{oid}/shipments", json=body, headers=headers).status_code == 422


def test_deliver_twice_and_unknown_shipment(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=2)
    oid = order["id"]
    sid = ship(client, h, oid, item_id, 2).json()["id"]
    assert client.post(f"{V2}/{oid}/shipments/{sid}/deliver", headers=h(DELIVERY)).status_code == 200
    assert client.post(f"{V2}/{oid}/shipments/{sid}/deliver", headers=h(DELIVERY)).status_code == 409
    assert client.post(f"{V2}/{oid}/shipments/99999/deliver", headers=h(DELIVERY)).status_code == 404


# ------------------------------------------------------------------ back-orders
def test_full_backorder_blocks_shipping_until_resolved(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=3)
    oid = order["id"]
    bo = client.post(f"{V2}/{oid}/items/{item_id}/backorder", headers=h(ORDER_MGR),
                     json={"quantity": 3, "reason": "Supplier delay", "expected_at": "2026-12-01T00:00:00"})
    assert bo.status_code == 201 and bo.json()["status"] == "OPEN"
    assert status_of(client, h, oid) == "BACKORDERED"
    assert ship(client, h, oid, item_id, 1).status_code == 409

    assert client.post(f"{V2}/{oid}/backorders/{bo.json()['id']}/resolve", headers=h(ORDER_MGR)).status_code == 200
    assert status_of(client, h, oid) == "PROCESSING"
    assert client.post(f"{V2}/{oid}/backorders/{bo.json()['id']}/resolve", headers=h(ORDER_MGR)).status_code == 409
    assert ship(client, h, oid, item_id, 3).status_code == 201
    assert status_of(client, h, oid) == "SHIPPED"


def test_partial_backorder_allows_partial_shipment(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=3)
    oid = order["id"]
    bo = client.post(f"{V2}/{oid}/items/{item_id}/backorder", headers=h(ORDER_MGR), json={"quantity": 1}).json()
    assert status_of(client, h, oid) == "CONFIRMED"                 # other units are still shippable
    assert ship(client, h, oid, item_id, 3).status_code == 409      # one unit is back-ordered
    assert ship(client, h, oid, item_id, 2).status_code == 201
    assert status_of(client, h, oid) == "PARTIALLY_SHIPPED"
    client.post(f"{V2}/{oid}/backorders/{bo['id']}/resolve", headers=h(ORDER_MGR))
    assert ship(client, h, oid, item_id, 1).status_code == 201
    assert status_of(client, h, oid) == "SHIPPED"


def test_backorder_validation_and_roles(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=2)
    oid = order["id"]
    url = f"{V2}/{oid}/items/{item_id}/backorder"
    assert client.post(url, headers=h(ORDER_MGR), json={"quantity": 3}).status_code == 409
    assert client.post(url, headers=h(ORDER_MGR), json={"quantity": 0}).status_code == 422
    assert client.post(f"{V2}/{oid}/items/999999/backorder", headers=h(ORDER_MGR), json={"quantity": 1}).status_code == 404
    assert client.post(url, headers=h(CUSTOMER), json={"quantity": 1}).status_code == 403
    assert client.post(url, headers=h(DELIVERY), json={"quantity": 1}).status_code == 403
    assert client.post(url, headers=h(UserRole.INVENTORY_MANAGER), json={"quantity": 1}).status_code == 201


# ------------------------------------------------------------------ item cancellation
def test_partial_item_cancel_restores_stock_and_refunds_proportionally(client, db, h, catalog, stripe_mock):
    order, item_id, item = make_order(client, h, catalog, stripe_mock, quantity=4)
    assert on_hand(db, catalog.product_id) == 6
    result = client.post(f"{V2}/{order['id']}/cancel-items", headers=h(CUSTOMER),
                         json={"reason": "Ordered too many", "items": [{"order_item_id": item_id, "quantity": 1}]})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["order_status"] == "CONFIRMED" and body["refund_status"] == "SUCCEEDED"
    expected = money(Decimal(str(item["line_total"])) / 4)
    assert Decimal(str(body["cancelled"][0]["refund_amount"])) == expected
    assert on_hand(db, catalog.product_id) == 7
    db.expire_all()
    refund = db.get(Refund, body["refund_id"])
    assert Decimal(str(refund.amount)) == expected and len(stripe_mock["refunds"]) == 1

    fulfilment = client.get(f"{V2}/{order['id']}/fulfilment", headers=h(CUSTOMER)).json()
    assert fulfilment["totals"]["cancelled"] == 1 and fulfilment["totals"]["remaining_to_ship"] == 3


def test_cancelling_everything_cancels_order_and_refunds_the_full_payment(client, db, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=3)
    client.post(f"{V2}/{order['id']}/cancel-items", headers=h(CUSTOMER),
                json={"reason": "Changed my mind", "items": [{"order_item_id": item_id, "quantity": 1}]})
    last = client.post(f"{V2}/{order['id']}/cancel-items", headers=h(CUSTOMER),
                       json={"reason": "Changed my mind", "items": [{"order_item_id": item_id, "quantity": 2}]})
    assert last.status_code == 200 and last.json()["order_status"] == "CANCELLED"
    assert on_hand(db, catalog.product_id) == 10
    db.expire_all()
    total_refunded = sum(Decimal(str(r.amount)) for r in db.query(Refund).all())
    assert total_refunded == Decimal(str(order["grand_total"]))      # shipping + tax included
    assert client.post(f"{V2}/{order['id']}/cancel-items", headers=h(CUSTOMER),
                       json={"reason": "again", "items": [{"order_item_id": item_id, "quantity": 1}]}).status_code == 409


def test_cannot_cancel_shipped_units(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=4)
    oid = order["id"]
    ship(client, h, oid, item_id, 2)
    too_many = client.post(f"{V2}/{oid}/cancel-items", headers=h(ORDER_MGR),
                           json={"reason": "stock issue", "items": [{"order_item_id": item_id, "quantity": 3}]})
    assert too_many.status_code == 409
    ok = client.post(f"{V2}/{oid}/cancel-items", headers=h(ORDER_MGR),
                     json={"reason": "stock issue", "items": [{"order_item_id": item_id, "quantity": 2}]})
    assert ok.status_code == 200 and ok.json()["order_status"] == "SHIPPED"   # nothing left to ship


def test_cancelling_the_unshipped_rest_after_delivery_completes_the_order(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=3)
    oid = order["id"]
    sid = ship(client, h, oid, item_id, 1).json()["id"]
    client.post(f"{V2}/{oid}/shipments/{sid}/deliver", headers=h(DELIVERY))
    client.post(f"{V2}/{oid}/cancel-items", headers=h(ORDER_MGR),
                json={"reason": "discontinued", "items": [{"order_item_id": item_id, "quantity": 2}]})
    assert status_of(client, h, oid) == "DELIVERED"                           # partial completion


def test_cancel_reduces_backorder_when_free_units_run_out(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=4)
    oid = order["id"]
    client.post(f"{V2}/{oid}/items/{item_id}/backorder", headers=h(ORDER_MGR), json={"quantity": 3})
    client.post(f"{V2}/{oid}/cancel-items", headers=h(ORDER_MGR),
                json={"reason": "no stock", "items": [{"order_item_id": item_id, "quantity": 3}]})
    totals = client.get(f"{V2}/{oid}/fulfilment", headers=h(ORDER_MGR)).json()["totals"]
    # 3 cancelled = 1 free unit + 2 taken from the back-order, which shrinks 3 -> 1
    assert totals["cancelled"] == 3 and totals["backordered"] == 1 and totals["remaining_to_ship"] == 0


def test_item_cancel_rules_for_customers(client, h, catalog, stripe_mock, second_customer):
    _, other = second_customer
    unpaid, unpaid_item, _ = make_order(client, h, catalog, stripe_mock, quantity=3, paid=False)
    url = f"{V2}/{unpaid['id']}/cancel-items"
    partial = {"reason": "too many", "items": [{"order_item_id": unpaid_item, "quantity": 1}]}
    assert client.post(url, headers=h(CUSTOMER), json=partial).status_code == 409      # unpaid partial
    assert client.post(url, headers=other, json=partial).status_code == 404            # someone else's order
    assert client.post(url, headers=h(CUSTOMER), json={"reason": "x", "items": []}).status_code == 422
    whole = {"reason": "not needed", "items": [{"order_item_id": unpaid_item, "quantity": 3}]}
    done = client.post(url, headers=h(CUSTOMER), json=whole)
    assert done.status_code == 200 and done.json()["order_status"] == "CANCELLED" and done.json()["refund_id"] is None

    paid, paid_item, _ = make_order(client, h, catalog, stripe_mock, quantity=2)
    client.post(f"{V2}/{paid['id']}/status", headers=h(ORDER_MGR), json={"status": "PROCESSING", "reason": "picking"})
    late = client.post(f"{V2}/{paid['id']}/cancel-items", headers=h(CUSTOMER),
                       json={"reason": "late", "items": [{"order_item_id": paid_item, "quantity": 1}]})
    assert late.status_code == 409                                                     # staff only once processing
    assert client.post(f"{V2}/{paid['id']}/cancel-items", headers=h(ORDER_MGR),
                       json={"reason": "late", "items": [{"order_item_id": paid_item, "quantity": 1}]}).status_code == 200


def test_cancel_items_is_idempotent(client, db, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=4)
    headers = {**h(CUSTOMER), "Idempotency-Key": "cancel-key-001"}
    body = {"reason": "oops", "items": [{"order_item_id": item_id, "quantity": 1}]}
    first = client.post(f"{V2}/{order['id']}/cancel-items", json=body, headers=headers)
    again = client.post(f"{V2}/{order['id']}/cancel-items", json=body, headers=headers)
    assert first.status_code == 200 and again.status_code == 200
    assert again.headers.get("Idempotent-Replay") == "true"
    assert on_hand(db, catalog.product_id) == 7 and len(stripe_mock["refunds"]) == 1


def test_phase1_cancel_after_item_cancel_does_not_restore_stock_twice(client, db, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=4)
    client.post(f"{V2}/{order['id']}/cancel-items", headers=h(CUSTOMER),
                json={"reason": "one less", "items": [{"order_item_id": item_id, "quantity": 1}]})
    whole = client.post(f"/orders/{order['id']}/cancel", headers=h(CUSTOMER), json={"reason": "cancel the rest"})
    assert whole.status_code == 200, whole.text
    assert on_hand(db, catalog.product_id) == 10          # not 11


def test_phase1_whole_order_cancel_closes_open_backorders(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=2)
    oid = order["id"]
    client.post(f"{V2}/{oid}/items/{item_id}/backorder", headers=h(ORDER_MGR), json={"quantity": 2})
    assert client.post(f"/orders/{oid}/cancel", headers=h(ORDER_MGR), json={"reason": "no stock"}).status_code == 200
    fulfilment = client.get(f"{V2}/{oid}/fulfilment", headers=h(ORDER_MGR)).json()
    assert fulfilment["backorders"][0]["status"] == "CANCELLED" and fulfilment["totals"]["backordered"] == 0


# ------------------------------------------------------------------ manual status + admin update
def test_manual_status_rules(client, h, catalog, stripe_mock):
    order, _, _ = make_order(client, h, catalog, stripe_mock, quantity=2)
    url = f"{V2}/{order['id']}/status"
    assert client.post(url, headers=h(CUSTOMER), json={"status": "PROCESSING", "reason": "go"}).status_code == 403
    assert client.post(url, headers=h(DELIVERY), json={"status": "PROCESSING", "reason": "go"}).status_code == 403
    assert client.post(url, headers=h(ORDER_MGR), json={"status": "PROCESSING"}).status_code == 422      # reason required
    assert client.post(url, headers=h(ORDER_MGR), json={"status": "NOPE", "reason": "go"}).status_code == 422
    assert client.post(url, headers=h(ORDER_MGR), json={"status": "SHIPPED", "reason": "go"}).status_code == 409
    assert client.post(url, headers=h(ORDER_MGR), json={"status": "CANCELLED", "reason": "go"}).status_code == 409
    assert client.post(url, headers=h(ORDER_MGR), json={"status": "CONFIRMED", "reason": "go"}).status_code == 409  # already
    ok = client.post(url, headers=h(ORDER_MGR), json={"status": "processing", "reason": "picking started"})
    assert ok.status_code == 200 and ok.json()["status"] == "PROCESSING"
    history = client.get(f"/orders/{order['id']}/status-history", headers=h(CUSTOMER)).json()
    assert history[-1]["note"] == "picking started" and history[-1]["changed_by"] is not None


def test_admin_update_changes_fields_and_is_audited(client, db, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=2)
    oid = order["id"]
    url = f"{V2}/{oid}"
    assert client.patch(url, headers=h(ORDER_MGR), json={"reason": "fix"}).status_code == 422          # nothing to change
    assert client.patch(url, headers=h(ORDER_MGR), json={"shipping_city": "Mysuru"}).status_code == 422  # reason required
    assert client.patch(url, headers=h(CUSTOMER), json={"reason": "fix", "shipping_city": "X"}).status_code == 403
    ok = client.patch(url, headers=h(ORDER_MGR), json={"reason": "Customer called", "shipping_city": "Mysuru",
                                                       "shipping_country": "us"})
    assert ok.status_code == 200 and set(ok.json()["changed_fields"]) == {"shipping_city", "shipping_country"}
    assert client.get(f"/orders/{oid}", headers=h(CUSTOMER)).json()["shipping_city"] == "Mysuru"
    db.expire_all()
    entry = db.query(AuditLog).filter(AuditLog.action == "ORDER_ADMIN_UPDATE").one()
    assert "Mysuru" not in (entry.details or "") and "shipping_city" in entry.details

    ship(client, h, oid, item_id, 1)
    assert client.patch(url, headers=h(ORDER_MGR), json={"reason": "late fix", "notes": "x"}).status_code == 409


# ------------------------------------------------------------------ Phase 1 compatibility + access
def test_phase1_status_endpoint_keeps_item_counters_in_sync(client, h, catalog, stripe_mock):
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=3)
    oid = order["id"]
    for new in ("PROCESSING", "SHIPPED"):
        assert client.patch(f"/orders/{oid}/status", headers=h(ORDER_MGR), json={"status": new}).status_code == 200
    totals = client.get(f"{V2}/{oid}/fulfilment", headers=h(ORDER_MGR)).json()["totals"]
    assert totals["shipped"] == 3 and totals["remaining_to_ship"] == 0
    assert ship(client, h, oid, item_id, 1).status_code == 409                  # nothing left to ship


def test_read_access_and_privacy(client, h, catalog, stripe_mock, second_customer):
    _, other = second_customer
    order, item_id, _ = make_order(client, h, catalog, stripe_mock, quantity=2)
    oid = order["id"]
    ship(client, h, oid, item_id, 1)
    for path in ("fulfilment", "timeline"):
        assert client.get(f"{V2}/{oid}/{path}", headers=other).status_code == 404
        assert client.get(f"{V2}/{oid}/{path}", headers=h(UserRole.SELLER)).status_code == 403
        assert client.get(f"{V2}/{oid}/{path}", headers=h(UserRole.SUPPORT_AGENT)).status_code == 200
        assert client.get(f"{V2}/999999/{path}", headers=h(ORDER_MGR)).status_code == 404
    assert client.get(f"{V2}/{oid}/timeline").status_code in (401, 403)

    mine = client.get(f"{V2}/{oid}/timeline", headers=h(CUSTOMER)).json()
    staff = client.get(f"{V2}/{oid}/timeline", headers=h(ORDER_MGR)).json()
    assert all(e["actor_id"] is None for e in mine)
    assert any(e["actor_id"] is not None for e in staff)
