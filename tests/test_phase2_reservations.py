"""Checkout stock holds: reserve, replace, release, expire, idempotency, checkout conversion."""
from datetime import timedelta

from app.models import Inventory, InventoryReservation, UserRole
from app.services.reservations import now_naive

from tests.conftest import checkout_payload

CUSTOMER = UserRole.CUSTOMER
INVENTORY = UserRole.INVENTORY_MANAGER
URL = "/v2/inventory/reservations"


def add_to_cart(client, headers, product_id, quantity):
    r = client.post("/cart/items", json={"product_id": product_id, "quantity": quantity}, headers=headers)
    assert r.status_code == 200, r.text


def stock(db, product_id):
    db.expire_all()
    return db.query(Inventory).filter(Inventory.product_id == product_id).one()


def test_reserve_cart_holds_stock(client, h, db, catalog):
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 3)
    response = client.post(URL, json={}, headers=h(CUSTOMER))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["reservations"][0]["quantity"] == 3
    assert body["reservations"][0]["status"] == "ACTIVE"
    inv = stock(db, catalog.product_id)
    assert inv.reserved_quantity == 3 and inv.available_quantity == 7 and inv.quantity_on_hand == 10


def test_reserve_empty_cart_is_rejected(client, h, catalog):
    client.get("/cart", headers=h(CUSTOMER))
    assert client.post(URL, json={}, headers=h(CUSTOMER)).status_code == 409


def test_cannot_reserve_more_than_available(client, h, db, catalog, second_customer):
    _, other = second_customer
    add_to_cart(client, other, catalog.product_id, 2)         # fits while nothing is held
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 9)
    assert client.post(URL, json={}, headers=h(CUSTOMER)).status_code == 201
    # only 1 unit is still available, the other customer needs 2
    assert client.post(URL, json={}, headers=other).status_code == 409
    assert stock(db, catalog.product_id).reserved_quantity == 9


def test_reserving_again_replaces_previous_hold(client, h, db, catalog):
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 2)
    client.post(URL, json={}, headers=h(CUSTOMER))
    client.post(URL, json={}, headers=h(CUSTOMER))
    assert stock(db, catalog.product_id).reserved_quantity == 2
    mine = client.get(f"{URL}/mine", headers=h(CUSTOMER)).json()
    assert mine["total"] == 2
    assert client.get(f"{URL}/mine?status=ACTIVE", headers=h(CUSTOMER)).json()["total"] == 1


def test_release_all_my_holds(client, h, db, catalog):
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 2)
    client.post(URL, json={}, headers=h(CUSTOMER))
    released = client.post(f"{URL}/mine/release", headers=h(CUSTOMER))
    assert released.status_code == 200 and released.json()["released"] == 1
    assert stock(db, catalog.product_id).reserved_quantity == 0
    # releasing again is harmless
    assert client.post(f"{URL}/mine/release", headers=h(CUSTOMER)).json()["released"] == 0


def test_release_one_checks_ownership_and_state(client, h, db, catalog, second_customer):
    _, other = second_customer
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 2)
    res_id = client.post(URL, json={}, headers=h(CUSTOMER)).json()["reservations"][0]["id"]

    assert client.post(f"{URL}/{res_id}/release", headers=other).status_code == 404
    assert client.post(f"{URL}/999999/release", headers=h(CUSTOMER)).status_code == 404

    ok = client.post(f"{URL}/{res_id}/release", headers=h(INVENTORY))
    assert ok.status_code == 200 and ok.json()["status"] == "RELEASED"
    assert client.post(f"{URL}/{res_id}/release", headers=h(CUSTOMER)).status_code == 409
    assert stock(db, catalog.product_id).reserved_quantity == 0


def test_expired_holds_are_released_by_sweep(client, h, db, catalog):
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 4)
    client.post(URL, json={"ttl_minutes": 5}, headers=h(CUSTOMER))
    db.expire_all()
    for res in db.query(InventoryReservation).all():
        res.expires_at = now_naive() - timedelta(minutes=1)
    db.commit()

    assert client.post(f"{URL}/expire", headers=h(CUSTOMER)).status_code == 403
    swept = client.post(f"{URL}/expire", headers=h(INVENTORY))
    assert swept.status_code == 200 and swept.json()["released"] == 1
    assert stock(db, catalog.product_id).reserved_quantity == 0
    assert db.query(InventoryReservation).one().status == "EXPIRED"
    assert client.post(f"{URL}/expire", headers=h(INVENTORY)).json()["released"] == 0


def test_ttl_boundaries(client, h, catalog):
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 1)
    assert client.post(URL, json={"ttl_minutes": 0}, headers=h(CUSTOMER)).status_code == 422
    assert client.post(URL, json={"ttl_minutes": 1441}, headers=h(CUSTOMER)).status_code == 422
    assert client.post(URL, json={"ttl_minutes": 1}, headers=h(CUSTOMER)).status_code == 201


def test_checkout_converts_the_customers_hold(client, h, db, catalog):
    headers = h(CUSTOMER)
    add_to_cart(client, headers, catalog.product_id, 2)
    client.post(URL, json={}, headers=headers)

    order = client.post("/checkout", json=checkout_payload(catalog.shipping_id), headers=headers)
    assert order.status_code == 201, order.text

    inv = stock(db, catalog.product_id)
    assert inv.quantity_on_hand == 8 and inv.reserved_quantity == 0
    res = db.query(InventoryReservation).one()
    assert res.status == "CONVERTED" and res.order_id == order.json()["id"]


def test_checkout_works_even_when_the_hold_covers_all_stock(client, h, db, catalog):
    headers = h(CUSTOMER)
    add_to_cart(client, headers, catalog.product_id, 10)
    assert client.post(URL, json={}, headers=headers).status_code == 201
    assert client.post("/checkout", json=checkout_payload(catalog.shipping_id), headers=headers).status_code == 201
    assert stock(db, catalog.product_id).quantity_on_hand == 0


def test_idempotency_key_replays_and_does_not_double_reserve(client, h, db, catalog):
    headers = {**h(CUSTOMER), "Idempotency-Key": "reserve-key-0001"}
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 3)

    first = client.post(URL, json={}, headers=headers)
    second = client.post(URL, json={}, headers=headers)
    assert first.status_code == 201 and second.status_code == 201
    assert second.headers.get("Idempotent-Replay") == "true"
    assert second.json()["reservations"][0]["group_id"] == first.json()["reservations"][0]["group_id"]
    assert stock(db, catalog.product_id).reserved_quantity == 3
    assert db.query(InventoryReservation).count() == 1

    changed = client.post(URL, json={"ttl_minutes": 30}, headers=headers)
    assert changed.status_code == 422


def test_failed_request_does_not_burn_the_idempotency_key(client, h, catalog):
    headers = {**h(CUSTOMER), "Idempotency-Key": "reserve-key-0002"}
    client.get("/cart", headers=h(CUSTOMER))
    assert client.post(URL, json={}, headers=headers).status_code == 409   # empty cart
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 1)
    assert client.post(URL, json={}, headers=headers).status_code == 201   # same key now works


def test_staff_listing_filters_and_is_forbidden_to_customers(client, h, catalog):
    add_to_cart(client, h(CUSTOMER), catalog.product_id, 1)
    client.post(URL, json={}, headers=h(CUSTOMER))
    assert client.get(URL, headers=h(CUSTOMER)).status_code == 403
    listing = client.get(f"{URL}?status=ACTIVE&product_id={catalog.product_id}", headers=h(INVENTORY))
    assert listing.status_code == 200 and listing.json()["total"] == 1
    assert client.get(f"{URL}?status=BOGUS", headers=h(INVENTORY)).status_code == 422
