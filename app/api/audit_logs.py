
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.dependencies import require_roles
from app.models.audit_log import AuditLog
from app.schemas.audit_logs import (
    AuditLogCreate,
    AuditLogResponse,
)
from app.services.audit import write_audit_log

router = APIRouter(
    prefix="/audit-logs",
    tags=["Audit Log Management"],
)

AUDIT_ROLES = ("SUPER_ADMIN", "ADMIN")


@router.post("", response_model=AuditLogResponse, status_code=201)
def create_audit_log(
    payload: AuditLogCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*AUDIT_ROLES)),
):
    return write_audit_log(
        db,
        actor_user_id=current_user.id,
        action=payload.action,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        outcome=payload.outcome,
        details=payload.details,
    )


@router.get("", response_model=list[AuditLogResponse])
def list_audit_logs(
    action: str | None = None,
    entity_type: str | None = None,
    actor_user_id: int | None = Query(default=None, gt=0),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*AUDIT_ROLES)),
):
    query = db.query(AuditLog)

    if action:
        query = query.filter(AuditLog.action == action)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    if actor_user_id is not None:
        query = query.filter(
            AuditLog.actor_user_id == actor_user_id
        )

    return (
        query.order_by(AuditLog.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
