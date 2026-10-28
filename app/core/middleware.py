"""Audit middleware: records every state-changing request by a signed-in user.

It decodes the bearer token itself (no DB lookup) so the actor is known even
when the endpoint rejects the call; the log row is written *after* the
response is produced and can never break the response.
"""
import logging
import re
import uuid

import jwt
from fastapi.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.audit import request_ip, write_audit_log

logger = logging.getLogger("shopsphere.audit")

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SKIP_PREFIXES = ("/payments/webhook", "/docs", "/openapi.json", "/redoc")
SENSITIVE_QUERY_KEYS = {"token", "password", "secret"}
ID_SEGMENT = re.compile(r"^\d+$")


def _actor_id(request: Request) -> int | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(
            header[7:].strip(),
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
        if payload.get("type") != "access":
            return None
        return int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        return None


def _write_entry(
    actor_id, method, route_path, path, status_code, ip, request_id, query
):
    segments = [s for s in path.strip("/").split("/") if s]
    entity_type = segments[0] if segments else "root"
    entity_id = next((s for s in segments[1:] if ID_SEGMENT.match(s)), None)

    if status_code < 400:
        outcome = "SUCCESS"
    elif status_code in (401, 403):
        outcome = "DENIED"
    else:
        outcome = "FAILED"

    db = SessionLocal()
    try:
        write_audit_log(
            db,
            actor_user_id=actor_id,
            action=f"{method} {route_path}"[:100],
            entity_type=entity_type[:100],
            entity_id=entity_id,
            outcome=outcome,
            details={"status_code": status_code, "query": query},
            ip_address=ip,
            request_id=request_id,
        )
    finally:
        db.close()


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        if (
            not settings.audit_enabled
            or request.method not in WRITE_METHODS
            or request.url.path.startswith(SKIP_PREFIXES)
        ):
            return response

        actor_id = _actor_id(request)
        if actor_id is None:
            return response

        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        query = {
            key: value
            for key, value in request.query_params.items()
            if key.lower() not in SENSITIVE_QUERY_KEYS
        }
        try:
            await run_in_threadpool(
                _write_entry,
                actor_id,
                request.method,
                route_path,
                request.url.path,
                response.status_code,
                request_ip(request),
                request_id,
                query,
            )
        except Exception:  # noqa: BLE001 - auditing must never break a request
            logger.exception("Could not write audit log entry")
        return response
