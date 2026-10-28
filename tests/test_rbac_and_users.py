import pytest

from app.core.permissions import ROLE_PERMISSIONS
from app.models import AuditLog, UserRole

from tests.conftest import ROLE_EMAILS, bearer, login, make_user

ALL_ROLES = list(UserRole)
STAFF = [r for r in UserRole if r != UserRole.CUSTOMER]


def test_every_role_has_a_permission_entry():
    assert set(ROLE_PERMISSIONS) == set(UserRole)


@pytest.mark.parametrize("role", [r for r in UserRole if r not in (UserRole.SUPER_ADMIN, UserRole.ADMIN)])
def test_only_admins_can_manage_users(client, h, role):
    assert client.get("/users", headers=h(role)).status_code == 403
    assert client.get("/audit-logs", headers=h(role)).status_code == 403


@pytest.mark.parametrize("role", [UserRole.SUPER_ADMIN, UserRole.ADMIN])
def test_admins_can_list_users(client, h, role):
    response = client.get("/users", headers=h(role))
    assert response.status_code == 200
    assert response.json()["total"] == len(UserRole)


def test_unauthenticated_requests_are_rejected(client):
    for path in ("/users", "/orders", "/cart", "/dashboard/overview", "/reports/catalog", "/audit-logs"):
        assert client.get(path).status_code in (401, 403), path


def test_customer_cannot_use_staff_endpoints(client, h):
    customer = h(UserRole.CUSTOMER)
    assert client.get("/orders/admin/all", headers=customer).status_code == 403
    assert client.get("/dashboard/overview", headers=customer).status_code == 403
    assert client.get("/reports/sales", headers=customer).status_code == 403
    assert client.get("/report-exports/inventory.csv", headers=customer).status_code == 403
    assert client.get("/notification-deliveries", headers=customer).status_code == 403


def test_staff_cannot_use_customer_only_cart(client, h):
    assert client.get("/cart", headers=h(UserRole.ADMIN)).status_code == 403


def test_admin_creates_staff_and_it_is_verified(client, h):
    response = client.post("/users", headers=h(UserRole.ADMIN), json={
        "full_name": "New Packer", "email": "packer@example.com",
        "password": "Packer#12345", "role": "ORDER_MANAGER",
    })
    assert response.status_code == 201
    assert response.json()["is_verified"] is True
    tokens = login(client, "packer@example.com", "Packer#12345")
    assert client.get("/orders/admin/all", headers=bearer(tokens)).status_code == 200


def test_admin_cannot_create_or_grant_admin_roles(client, h, users):
    body = {"full_name": "Another Admin", "email": "a2@example.com", "password": "Admin#12345", "role": "ADMIN"}
    assert client.post("/users", headers=h(UserRole.ADMIN), json=body).status_code == 403
    assert client.post("/users", headers=h(UserRole.SUPER_ADMIN), json=body).status_code == 201

    target = users[UserRole.CUSTOMER].id
    assert client.put(f"/users/{target}/role", headers=h(UserRole.ADMIN), json={"role": "SUPER_ADMIN"}).status_code == 403


def test_admin_cannot_modify_super_admin(client, h, users):
    target = users[UserRole.SUPER_ADMIN].id
    assert client.post(f"/users/{target}/deactivate", headers=h(UserRole.ADMIN)).status_code == 403
    assert client.patch(f"/users/{target}", headers=h(UserRole.ADMIN), json={"full_name": "Hacked"}).status_code == 403


def test_role_change_takes_effect_and_forces_relogin(client, h, users):
    customer_tokens = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    target = users[UserRole.CUSTOMER].id
    response = client.put(f"/users/{target}/role", headers=h(UserRole.SUPER_ADMIN), json={"role": "SUPPORT_AGENT"})
    assert response.status_code == 200
    assert client.get("/auth/me", headers=bearer(customer_tokens)).status_code == 401
    fresh = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    assert client.get("/auth/me", headers=bearer(fresh)).json()["role"] == "SUPPORT_AGENT"


def test_cannot_change_own_role_or_status(client, h, users):
    me = users[UserRole.SUPER_ADMIN].id
    assert client.put(f"/users/{me}/role", headers=h(UserRole.SUPER_ADMIN), json={"role": "ADMIN"}).status_code == 409
    assert client.post(f"/users/{me}/deactivate", headers=h(UserRole.SUPER_ADMIN)).status_code == 409


def test_deactivate_and_reactivate_user(client, h, users):
    target = users[UserRole.CUSTOMER].id
    tokens = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    assert client.post(f"/users/{target}/deactivate", headers=h(UserRole.ADMIN)).json()["is_active"] is False
    assert client.get("/auth/me", headers=bearer(tokens)).status_code == 401
    assert client.post("/auth/login", json={"email": ROLE_EMAILS[UserRole.CUSTOMER], "password": "Passw0rd!123"}).status_code == 401
    assert client.post(f"/users/{target}/activate", headers=h(UserRole.ADMIN)).json()["is_active"] is True


def test_search_and_filter_users(client, h):
    admin = h(UserRole.ADMIN)
    by_role = client.get("/users", params={"role": "CUSTOMER"}, headers=admin).json()
    assert by_role["total"] == 1
    by_text = client.get("/users", params={"q": "finance_manager"}, headers=admin).json()
    assert [u["role"] for u in by_text["items"]] == ["FINANCE_MANAGER"]
    inactive = client.get("/users", params={"is_active": "false"}, headers=admin).json()
    assert inactive["total"] == 0
    assert client.get("/users", params={"limit": 2}, headers=admin).json()["items"].__len__() == 2


def test_role_permission_matrix_endpoint(client, h):
    matrix = client.get("/users/roles/permissions", headers=h(UserRole.ADMIN)).json()
    assert set(matrix) == {r.value for r in UserRole}
    assert matrix["SUPER_ADMIN"] == ["*"]


def test_state_changing_calls_are_audited_with_actor(client, db, h, users):
    client.post("/users", headers=h(UserRole.ADMIN), json={
        "full_name": "Audit Me", "email": "audit@example.com", "password": "Audit#12345", "role": "SUPPORT_AGENT",
    })
    db.expire_all()
    rows = db.query(AuditLog).filter(AuditLog.action == "POST /users").all()
    assert rows and rows[-1].actor_user_id == users[UserRole.ADMIN].id and rows[-1].outcome == "SUCCESS"


def test_denied_attempts_are_audited(client, db, h, users):
    client.post("/users", headers=h(UserRole.CUSTOMER), json={
        "full_name": "Sneaky", "email": "s@example.com", "password": "Sneaky#12345", "role": "ADMIN",
    })
    db.expire_all()
    row = db.query(AuditLog).filter(AuditLog.outcome == "DENIED").first()
    assert row is not None and row.actor_user_id == users[UserRole.CUSTOMER].id


def test_audit_log_has_no_update_or_delete_api(client, h):
    admin = h(UserRole.SUPER_ADMIN)
    for method in (client.put, client.patch, client.delete):
        assert method("/audit-logs/1", headers=admin).status_code in (404, 405)


def test_audit_log_listing_for_admin_only(client, h):
    client.post("/users", headers=h(UserRole.ADMIN), json={
        "full_name": "Listed", "email": "l@example.com", "password": "Listed#12345", "role": "SUPPORT_AGENT",
    })
    response = client.get("/audit-logs", params={"action": "POST /users"}, headers=h(UserRole.ADMIN))
    assert response.status_code == 200 and len(response.json()) >= 1
