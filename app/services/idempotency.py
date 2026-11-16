"""Idempotency-Key support for money- and stock-moving endpoints.

Usage inside an endpoint::

    replay = begin(db, scope="reservation.create", key=key, user_id=u.id, payload={...})
    if replay is not None:
        return JSONResponse(replay.response_body, status_code=replay.response_status)
    ...do the work...
    complete(db, record, 201, body)     # same transaction as the work

Same key + same payload  -> stored response is replayed.
Same key + other payload -> 422 (key reuse for a different request).
Same key while the first call is still running -> 409.
"""
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import utcnow
from app.models.idempotency import IdempotencyKey


def hash_payload(payload) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Begun:
    """Returned for a brand-new key; carries the row to complete later."""

    def __init__(self, record: IdempotencyKey):
        self.record = record


def begin(db: Session, *, scope: str, key: str, user_id: int | None, payload) -> IdempotencyKey | Begun:
    """Return a completed record to replay, or ``Begun`` for a new key."""
    digest = hash_payload(payload)
    existing = db.scalar(
        select(IdempotencyKey)
        .where(IdempotencyKey.scope == scope, IdempotencyKey.key == key)
        .with_for_update()
    )
    if existing is None:
        record = IdempotencyKey(scope=scope, key=key, user_id=user_id, request_hash=digest)
        db.add(record)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()  # lost a race with a concurrent identical request
            raise HTTPException(status_code=409, detail="A request with this Idempotency-Key is in progress")
        return Begun(record)

    if existing.user_id != user_id or existing.request_hash != digest:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key was already used with a different request",
        )
    if existing.status != "COMPLETED":
        raise HTTPException(status_code=409, detail="A request with this Idempotency-Key is in progress")
    return existing


def complete(db: Session, begun: Begun, status_code: int, body) -> None:
    rec = begun.record
    rec.status = "COMPLETED"
    rec.response_status = status_code
    rec.response_body = json.loads(json.dumps(body, default=str))
    rec.completed_at = utcnow().replace(tzinfo=None)


def start(db: Session, *, scope: str, key: str | None, user_id: int | None, payload):
    """Convenience for endpoints. Returns (begun, replay_json_response).

    * no key           -> (None, None)  - proceed without idempotency
    * new key          -> (Begun, None) - proceed, then call ``complete``
    * completed key    -> (None, JSONResponse) - return it as-is
    """
    if not key:
        return None, None
    from fastapi.responses import JSONResponse

    outcome = begin(db, scope=scope, key=key, user_id=user_id, payload=payload)
    if isinstance(outcome, Begun):
        return outcome, None
    return None, JSONResponse(
        outcome.response_body,
        status_code=outcome.response_status,
        headers={"Idempotent-Replay": "true"},
    )
