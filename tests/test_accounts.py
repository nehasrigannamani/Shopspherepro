"""Profile, address and deactivation-request behaviour."""
from app.models import Order, UserRole

from tests.conftest import bearer, checkout_payload, login, place_order, ROLE_EMAILS

CUSTOMER = UserRole.CUSTOMER

ADDRESS = {
    "address_type": "SHIPPING", "label": "Home", "full_name": "Chris Customer",
    "phone": "+919000000001", "line1": "12 MG Road", "city": "Bengaluru",
    "state": "Karnataka", "postal_code": "560001", "country": "in",
}


def add_address(client, headers, **overrides):
    response = client.post("/addresses", json={**ADDRESS, **overrides}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_first_address_becomes_default_and_country_is_normalised(client, h):
    address = add_address(client, h(CUSTOMER))
    assert address["is_default_shipping"] is True
    assert address["is_default_billing"] is False
    assert address["country"] == "IN"


def test_default_moves_when_another_default_is_chosen(client, h):
    headers = h(CUSTOMER)
    first = add_address(client, headers)
    second = add_address(client, headers, label="Office", is_default=True)
    listing = {a["id"]: a for a in client.get("/addresses", headers=headers).json()}
    assert listing[second["id"]]["is_default_shipping"] is True
    assert listing[first["id"]]["is_default_shipping"] is False

    client.put(f"/addresses/{first['id']}/default", params={"kind": "SHIPPING"}, headers=headers)
    listing = {a["id"]: a for a in client.get("/addresses", headers=headers).json()}
    assert listing[first["id"]]["is_default_shipping"] is True
    assert sum(a["is_default_shipping"] for a in listing.values()) == 1


def test_deleting_default_promotes_another(client, h):
    headers = h(CUSTOMER)
    first = add_address(client, headers)
    second = add_address(client, headers, is_default=True)
    assert client.delete(f"/addresses/{second['id']}", headers=headers).status_code == 204
    remaining = client.get("/addresses", headers=headers).json()
    assert [a["id"] for a in remaining] == [first["id"]]
    assert remaining[0]["is_default_shipping"] is True


def test_billing_and_shipping_defaults_are_independent(client, h):
    headers = h(CUSTOMER)
    ship = add_address(client, headers)
    bill = add_address(client, headers, address_type="BILLING", label="Billing")
    assert bill["is_default_billing"] is True and bill["is_default_shipping"] is False
    assert client.put(f"/addresses/{bill['id']}/default", params={"kind": "SHIPPING"}, headers=headers).status_code == 409
    assert [a["id"] for a in client.get("/addresses", params={"address_type": "SHIPPING"}, headers=headers).json()] == [ship["id"]]


def test_address_validation(client, h):
    headers = h(CUSTOMER)
    for bad in ({"country": "India"}, {"postal_code": "!!"}, {"phone": "abc"}, {"line1": ""}, {"address_type": "OTHER"}):
        assert client.post("/addresses", json={**ADDRESS, **bad}, headers=headers).status_code == 422, bad


def test_addresses_are_isolated_between_customers(client, h, second_customer):
    _, other = second_customer
    mine = add_address(client, h(CUSTOMER))
    assert client.get(f"/addresses/{mine['id']}", headers=other).status_code == 404
    assert client.patch(f"/addresses/{mine['id']}", json={"city": "Pune"}, headers=other).status_code == 404
    assert client.delete(f"/addresses/{mine['id']}", headers=other).status_code == 404
    assert client.get("/addresses", headers=other).json() == []
    assert client.get("/addresses", params={"user_id": 1}, headers=other).status_code == 403


def test_support_can_view_a_customers_addresses(client, h, users):
    add_address(client, h(CUSTOMER))
    listing = client.get("/addresses", params={"user_id": users[CUSTOMER].id}, headers=h(UserRole.SUPPORT_AGENT))
    assert listing.status_code == 200 and len(listing.json()) == 1


def test_update_address(client, h):
    headers = h(CUSTOMER)
    address = add_address(client, headers)
    updated = client.patch(f"/addresses/{address['id']}", json={"city": "Mysuru"}, headers=headers)
    assert updated.status_code == 200 and updated.json()["city"] == "Mysuru"
    assert client.patch(f"/addresses/{address['id']}", json={}, headers=headers).status_code == 422


def test_checkout_with_saved_address_snapshots_it_on_the_order(client, db, h, catalog):
    headers = h(CUSTOMER)
    address = add_address(client, headers, address_type="BOTH")
    client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": 1}, headers=headers)
    order = client.post(
        "/checkout",
        json={"shipping_method_id": catalog.shipping_id, "shipping_address_id": address["id"]},
        headers=headers,
    ).json()
    assert order["shipping_address_id"] == address["id"]
    assert order["billing_address_id"] == address["id"]
    assert order["shipping_city"] == "Bengaluru"

    # Editing or deleting the address later never rewrites the order.
    client.patch(f"/addresses/{address['id']}", json={"city": "Pune"}, headers=headers)
    client.delete(f"/addresses/{address['id']}", headers=headers)
    stored = client.get(f"/orders/{order['id']}", headers=headers).json()
    assert stored["shipping_city"] == "Bengaluru"


def test_checkout_rejects_foreign_or_wrong_type_addresses(client, h, catalog, second_customer):
    _, other = second_customer
    foreign = add_address(client, other)
    headers = h(CUSTOMER)
    billing_only = add_address(client, headers, address_type="BILLING")
    client.post("/cart/items", json={"product_id": catalog.product_id}, headers=headers)

    body = {"shipping_method_id": catalog.shipping_id}
    assert client.post("/checkout", json={**body, "shipping_address_id": foreign["id"]}, headers=headers).status_code == 404
    assert client.post("/checkout", json={**body, "shipping_address_id": billing_only["id"]}, headers=headers).status_code == 409


# ---------------------------------------------------------------- profile ---

def test_profile_is_only_the_callers_own(client, h):
    assert client.get("/profile", headers=h(CUSTOMER)).json()["email"] == ROLE_EMAILS[CUSTOMER]
    assert "hashed_password" not in client.get("/profile", headers=h(CUSTOMER)).json()


def test_profile_update_records_history(client, h):
    headers = h(CUSTOMER)
    assert client.patch("/profile", json={"full_name": "Chris Updated", "phone": "+919111111111"}, headers=headers).status_code == 200
    assert client.patch("/profile", json={}, headers=headers).status_code == 422
    history = client.get("/profile/history", headers=headers).json()
    changes = {row["field_name"]: (row["old_value"], row["new_value"]) for row in history}
    assert changes["full_name"][1] == "Chris Updated"
    assert changes["phone"] == (None, "+919111111111")


def test_profile_rejects_bad_or_duplicate_phone(client, h, second_customer):
    other, _ = second_customer
    headers = h(CUSTOMER)
    assert client.patch("/profile", json={"phone": "12345"}, headers=headers).status_code == 422
    client.patch("/profile", json={"phone": "+919222222222"}, headers=h(CUSTOMER))
    from tests.conftest import bearer as _b
    assert client.patch("/profile", json={"phone": "+919222222222"}, headers=_b(login(client, other.email))).status_code == 409


def test_profile_history_is_private(client, h, second_customer):
    _, other = second_customer
    client.patch("/profile", json={"full_name": "Changed Name"}, headers=h(CUSTOMER))
    assert client.get("/profile/history", headers=other).json() == []


def test_deactivation_request_flow(client, h):
    headers = h(CUSTOMER)
    created = client.post("/profile/deactivation-requests", json={"reason": "leaving"}, headers=headers)
    assert created.status_code == 201 and created.json()["status"] == "PENDING"
    assert client.post("/profile/deactivation-requests", json={}, headers=headers).status_code == 409
    assert len(client.get("/profile/deactivation-requests/mine", headers=headers).json()) == 1

    assert client.get("/profile/deactivation-requests", headers=headers).status_code == 403
    pending = client.get("/profile/deactivation-requests", params={"status": "PENDING"}, headers=h(UserRole.SUPPORT_AGENT))
    assert len(pending.json()) == 1

    request_id = created.json()["id"]
    assert client.post(f"/profile/deactivation-requests/{request_id}/decision", json={"status": "APPROVED"}, headers=h(UserRole.SUPPORT_AGENT)).status_code == 403
    approved = client.post(f"/profile/deactivation-requests/{request_id}/decision", json={"status": "APPROVED"}, headers=h(UserRole.ADMIN))
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.post(f"/profile/deactivation-requests/{request_id}/decision", json={"status": "REJECTED"}, headers=h(UserRole.ADMIN)).status_code == 409


def test_rejected_deactivation_keeps_account_active(client, h):
    headers = h(CUSTOMER)
    request_id = client.post("/profile/deactivation-requests", json={}, headers=headers).json()["id"]
    client.post(f"/profile/deactivation-requests/{request_id}/decision", json={"status": "REJECTED", "decision_note": "stay"}, headers=h(UserRole.ADMIN))
    assert client.get("/auth/me", headers=headers).status_code == 200
