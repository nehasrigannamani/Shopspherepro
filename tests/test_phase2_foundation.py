"""Phase 2 foundation: error envelope, rate limit, redaction, pagination, idempotency, SELLER role."""
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.log_filter import redact
from app.core.ratelimit import rate_limit
from app.models import UserRole
from app.services import idempotency

from tests.conftest import make_user


def test_error_envelope_keeps_legacy_detail(client, h):
    response = client.get("/users", headers=h(UserRole.CUSTOMER))
    assert response.status_code == 403
    body = response.json()
    assert body["detail"]
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["error"]["message"] == body["detail"]


def test_validation_error_has_fields_and_does_not_echo_input(client):
    secret = "Zq9short"
    response = client.post(
        "/auth/register",
        json={"full_name": "A", "email": "not-an-email", "password": secret},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["fields"]
    assert secret not in response.text


def test_unauthenticated_is_rejected_with_envelope(client):
    response = client.get("/v2/inventory/reservations/mine")
    assert response.status_code in (401, 403)
    assert "error" in response.json()


def test_rate_limit_returns_429_with_retry_after():
    mini = FastAPI()

    @mini.get("/ping", dependencies=[Depends(rate_limit(2))])
    def ping():
        return {"ok": True}

    c = TestClient(mini)
    assert c.get("/ping").status_code == 200
    assert c.get("/ping").status_code == 200
    blocked = c.get("/ping")
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


@pytest.mark.parametrize(
    "raw, leaked",
    [
        ("Authorization: Bearer abc123.def456.ghi789", "abc123"),
        ("stripe key sk_test_51HabcdefGHI", "51Habcdef"),
        ("whsec_abcdef123456", "abcdef123456"),
        ('{"password": "hunter2hunter2"}', "hunter2"),
        ("card 4242 4242 4242 4242 charged", "4242 4242"),
    ],
)
def test_log_redaction_masks_sensitive_values(raw, leaked):
    assert leaked not in redact(raw)


def test_page_size_is_capped(client, h):
    headers = h(UserRole.CUSTOMER)
    assert client.get("/v2/inventory/reservations/mine?page_size=101", headers=headers).status_code == 422
    assert client.get("/v2/inventory/reservations/mine?page=0", headers=headers).status_code == 422
    assert client.get("/v2/inventory/reservations/mine?page_size=100", headers=headers).status_code == 200


def test_idempotency_replay_conflict_and_other_user(db, users):
    customer = users[UserRole.CUSTOMER]
    other = make_user(db, "other@test.local")
    kwargs = dict(scope="t.scope", key="key-12345678", payload={"a": 1})

    first = idempotency.begin(db, user_id=customer.id, **kwargs)
    assert isinstance(first, idempotency.Begun)

    # Same key while the first call has not completed -> in progress.
    with pytest.raises(HTTPException) as in_progress:
        idempotency.begin(db, user_id=customer.id, **kwargs)
    assert in_progress.value.status_code == 409

    idempotency.complete(db, first, 201, {"id": 7})
    db.commit()

    replay = idempotency.begin(db, user_id=customer.id, **kwargs)
    assert not isinstance(replay, idempotency.Begun)
    assert replay.response_status == 201 and replay.response_body == {"id": 7}

    with pytest.raises(HTTPException) as changed:
        idempotency.begin(db, user_id=customer.id, scope="t.scope", key="key-12345678", payload={"a": 2})
    assert changed.value.status_code == 422

    with pytest.raises(HTTPException) as foreign:
        idempotency.begin(db, user_id=other.id, **kwargs)
    assert foreign.value.status_code == 422


def test_seller_role_is_not_staff_and_not_customer(client, h):
    seller = h(UserRole.SELLER)
    assert client.get("/users", headers=seller).status_code == 403
    assert client.get("/cart", headers=seller).status_code == 403
    assert client.get("/v2/inventory/reservations", headers=seller).status_code == 403
    assert client.get("/v2/inventory/reservations/mine", headers=seller).status_code == 403
