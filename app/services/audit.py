
import json

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def write_audit_log(
    db: Session,
    *,
    actor_user_id: int | None,
    action: str,
    entity_type: str,
    entity_id: str | int | None = None,
    outcome: str = "SUCCESS",
    details: dict | str | None = None,
    ip_address: str | None = None,
    request_id: str | None = None,
    commit: bool = True,
):
    if isinstance(details, dict):
        details_text = json.dumps(
            details,
            default=str,
            ensure_ascii=False,
        )
    else:
        details_text = details

    entry = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=(
            None if entity_id is None else str(entity_id)
        ),
        outcome=outcome,
        details=details_text,
        ip_address=ip_address,
        request_id=request_id,
    )

    db.add(entry)

    if commit:
        db.commit()
        db.refresh(entry)

    return entry


def request_ip(request: Request) -> str | None:
    # Forwarded headers are not trusted unless a trusted proxy is configured.
    return request.client.host if request.client else None
