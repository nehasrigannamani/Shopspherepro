import re

import jwt

from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token
from app.models import NotificationDelivery, User, UserRole

from tests.conftest import PASSWORD, ROLE_EMAILS, bearer, login, make_user


def register(client, **overrides):
    payload = {
        "full_name": "New Customer",
        "email": "new@example.com",
        "phone": "+919876543210",
        "password": "Sup3rSecret!",
    }
    payload.update(overrides)
    return client.post("/auth/register", json=payload)


def test_register_creates_unverified_customer(client, db):
    response = register(client)
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "CUSTOMER"
    assert body["is_verified"] is False
    assert "password" not in body and "hashed_password" not in body


def test_register_rejects_duplicate_email_and_phone(client):
    assert register(client).status_code == 201
    assert register(client).status_code == 409
    assert register(client, email="other@example.com").status_code == 409  # same phone


def test_register_normalises_email_case(client):
    assert register(client, email="Mixed@Example.com").status_code == 201
    assert register(client, email="mixed@example.com", phone="+919876543211").status_code == 409


def test_register_validates_phone_and_password(client):
    assert register(client, phone="12345").status_code == 422
    assert register(client, password="short1").status_code == 422
    assert register(client, password="onlyletters").status_code == 422
    assert register(client, email="not-an-email").status_code == 422


def test_login_success_returns_access_and_refresh_tokens(client, users):
    tokens = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["expires_in"] == settings.access_token_expire_minutes * 60


def test_login_wrong_password_and_unknown_user_look_identical(client, users):
    bad_pw = client.post("/auth/login", json={"email": ROLE_EMAILS[UserRole.CUSTOMER], "password": "wrong"})
    unknown = client.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong"})
    assert bad_pw.status_code == unknown.status_code == 401
    assert bad_pw.json() == unknown.json()


def test_inactive_user_cannot_login(client, db, users):
    users[UserRole.CUSTOMER].is_active = False
    db.commit()
    response = client.post("/auth/login", json={"email": ROLE_EMAILS[UserRole.CUSTOMER], "password": PASSWORD})
    assert response.status_code == 401


def test_protected_route_requires_token(client):
    assert client.get("/auth/me").status_code in (401, 403)
    assert client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_me_returns_current_user(client, h):
    response = client.get("/auth/me", headers=h(UserRole.CUSTOMER))
    assert response.status_code == 200
    assert response.json()["email"] == ROLE_EMAILS[UserRole.CUSTOMER]


def test_refresh_token_cannot_be_used_as_access_token(client, users):
    tokens = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert response.status_code == 401


def test_access_token_cannot_be_used_to_refresh(client, users):
    tokens = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    assert client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]}).status_code == 401


def test_tampered_or_unsigned_token_rejected(client, users):
    forged = jwt.encode({"sub": "1", "type": "access", "sid": "x"}, "wrong-key", algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_access_token_without_session_is_rejected(client, users):
    token = create_access_token(users[UserRole.CUSTOMER].id, session_id="doesnotexist")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_refresh_rotates_tokens(client, users):
    tokens = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    response = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200
    renewed = response.json()
    assert renewed["refresh_token"] != tokens["refresh_token"]
    assert client.get("/auth/me", headers=bearer(renewed)).status_code == 200


def test_refresh_token_reuse_revokes_the_whole_session(client, users):
    tokens = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    renewed = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()

    replay = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401
    # Theft assumed: even the newest tokens of that session stop working.
    assert client.get("/auth/me", headers=bearer(renewed)).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": renewed["refresh_token"]}).status_code == 401


def test_logout_revokes_access_and_refresh(client, users):
    tokens = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    assert client.post("/auth/logout", headers=bearer(tokens)).status_code == 204
    assert client.get("/auth/me", headers=bearer(tokens)).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_logout_only_affects_current_session(client, users):
    first = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    second = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    client.post("/auth/logout", headers=bearer(first))
    assert client.get("/auth/me", headers=bearer(second)).status_code == 200


def test_logout_all_revokes_every_session(client, users):
    first = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    second = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    assert client.post("/auth/logout-all", headers=bearer(first)).status_code == 204
    assert client.get("/auth/me", headers=bearer(second)).status_code == 401


def test_deactivated_user_loses_access_immediately(client, db, users):
    tokens = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    users[UserRole.CUSTOMER].is_active = False
    db.commit()
    assert client.get("/auth/me", headers=bearer(tokens)).status_code == 401


def test_change_password_keeps_current_session_only(client, users):
    current = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    other = login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    wrong = client.post("/auth/change-password", headers=bearer(current),
                        json={"current_password": "nope", "new_password": "BrandNew#123"})
    assert wrong.status_code == 400
    ok = client.post("/auth/change-password", headers=bearer(current),
                     json={"current_password": PASSWORD, "new_password": "BrandNew#123"})
    assert ok.status_code == 204
    assert client.get("/auth/me", headers=bearer(current)).status_code == 200
    assert client.get("/auth/me", headers=bearer(other)).status_code == 401
    assert client.post("/auth/login", json={"email": ROLE_EMAILS[UserRole.CUSTOMER], "password": "BrandNew#123"}).status_code == 200


def _verification_token(db, user_id):
    delivery = (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.user_id == user_id)
        .order_by(NotificationDelivery.id.desc())
        .first()
    )
    assert delivery is not None, "verification email was not queued"
    return re.search(r"token=([A-Za-z0-9_\-]+)", delivery.message).group(1)


def test_email_verification_flow(client, db):
    user_id = register(client).json()["id"]
    token = _verification_token(db, user_id)

    assert client.post("/auth/verify-email", json={"token": "x" * 20}).status_code == 400

    response = client.post("/auth/verify-email", json={"token": token})
    assert response.status_code == 200
    assert response.json()["is_verified"] is True

    # Tokens are single use.
    assert client.post("/auth/verify-email", json={"token": token}).status_code == 400


def test_resend_verification_only_for_unverified_users(client, db):
    register(client)
    tokens = login(client, "new@example.com", "Sup3rSecret!")
    first = client.post("/auth/resend-verification", headers=bearer(tokens))
    assert first.status_code == 202

    token = _verification_token(db, db.query(User).filter(User.email == "new@example.com").one().id)
    client.post("/auth/verify-email", json={"token": token})
    assert client.post("/auth/resend-verification", headers=bearer(tokens)).status_code == 409


def test_login_is_recorded_in_user_activity(client, db, users, h):
    login(client, ROLE_EMAILS[UserRole.CUSTOMER])
    response = client.get(f"/users/{users[UserRole.CUSTOMER].id}/activity", headers=h(UserRole.ADMIN))
    assert response.status_code == 200
    assert "LOGIN" in {row["action"] for row in response.json()}
