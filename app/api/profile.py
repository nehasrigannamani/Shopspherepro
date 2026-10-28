from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_roles
from app.db.session import get_db
from app.models.account import DeactivationRequest, ProfileChangeHistory
from app.models.user import User, UserRole
from app.schemas.users import (
    DeactivationDecision,
    DeactivationRequestCreate,
    DeactivationRequestResponse,
    ProfileHistoryResponse,
    ProfileResponse,
    ProfileUpdate,
)
from app.services.accounts import record_activity, revoke_all_sessions
from app.services.audit import write_audit_log
from app.services.notify import create_notification, notify_roles

router = APIRouter(prefix="/profile", tags=["Customer Profile Management"])

REVIEWERS = (UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.SUPPORT_AGENT)


@router.get("", response_model=ProfileResponse)
def get_profile(current_user: User = Depends(get_current_user)):
    """Only the signed-in user's own profile is ever returned here."""
    return current_user


@router.patch("", response_model=ProfileResponse)
def update_profile(
    payload: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No changes supplied")

    if changes.get("phone"):
        taken = (
            db.query(User.id)
            .filter(User.phone == changes["phone"], User.id != current_user.id)
            .first()
        )
        if taken:
            raise HTTPException(status_code=409, detail="Phone already in use")

    for field, new_value in changes.items():
        if isinstance(new_value, str):
            new_value = new_value.strip()
        old_value = getattr(current_user, field)
        if old_value == new_value:
            continue
        db.add(
            ProfileChangeHistory(
                user_id=current_user.id,
                changed_by=current_user.id,
                field_name=field,
                old_value=None if old_value is None else str(old_value),
                new_value=None if new_value is None else str(new_value),
            )
        )
        setattr(current_user, field, new_value)

    record_activity(db, current_user.id, "PROFILE_UPDATED", ",".join(sorted(changes)))
    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/history", response_model=list[ProfileHistoryResponse])
def get_profile_history(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(ProfileChangeHistory)
        .filter(ProfileChangeHistory.user_id == current_user.id)
        .order_by(ProfileChangeHistory.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.post(
    "/deactivation-requests",
    response_model=DeactivationRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
def request_deactivation(
    payload: DeactivationRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    pending = (
        db.query(DeactivationRequest.id)
        .filter(
            DeactivationRequest.user_id == current_user.id,
            DeactivationRequest.status == "PENDING",
        )
        .first()
    )
    if pending:
        raise HTTPException(
            status_code=409, detail="You already have a pending deactivation request"
        )

    request_row = DeactivationRequest(
        user_id=current_user.id, reason=payload.reason, status="PENDING"
    )
    db.add(request_row)
    db.flush()
    notify_roles(
        db,
        (UserRole.ADMIN, UserRole.SUPPORT_AGENT),
        "Account deactivation requested",
        f"User {current_user.id} asked to deactivate their account.",
        notification_type="ACCOUNT",
        reference_type="deactivation_request",
        reference_id=request_row.id,
    )
    record_activity(db, current_user.id, "DEACTIVATION_REQUESTED")
    db.commit()
    db.refresh(request_row)
    return request_row


@router.get(
    "/deactivation-requests/mine",
    response_model=list[DeactivationRequestResponse],
)
def my_deactivation_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(DeactivationRequest)
        .filter(DeactivationRequest.user_id == current_user.id)
        .order_by(DeactivationRequest.id.desc())
        .all()
    )


@router.get(
    "/deactivation-requests",
    response_model=list[DeactivationRequestResponse],
)
def list_deactivation_requests(
    request_status: str | None = Query(
        default=None, alias="status", pattern=r"^(PENDING|APPROVED|REJECTED)$"
    ),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*REVIEWERS)),
):
    query = db.query(DeactivationRequest)
    if request_status:
        query = query.filter(DeactivationRequest.status == request_status)
    return query.order_by(DeactivationRequest.id.desc()).offset(skip).limit(limit).all()


@router.post(
    "/deactivation-requests/{request_id}/decision",
    response_model=DeactivationRequestResponse,
)
def decide_deactivation(
    request_id: int,
    payload: DeactivationDecision,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)),
):
    row = (
        db.query(DeactivationRequest)
        .filter(DeactivationRequest.id == request_id)
        .with_for_update()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if row.status != "PENDING":
        raise HTTPException(status_code=409, detail="Request was already decided")

    row.status = payload.status
    row.decided_by = current_user.id
    row.decision_note = payload.decision_note
    row.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)

    target = db.get(User, row.user_id)
    if payload.status == "APPROVED" and target is not None:
        if target.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN) and current_user.role != UserRole.SUPER_ADMIN:
            raise HTTPException(
                status_code=403, detail="Only a super admin can deactivate admin accounts"
            )
        target.is_active = False
        revoke_all_sessions(db, target.id, "account_deactivated")
        record_activity(db, target.id, "DEACTIVATED", "approved deactivation request")

    write_audit_log(
        db, actor_user_id=current_user.id, action="DEACTIVATION_DECIDED",
        entity_type="deactivation_request", entity_id=row.id,
        details={"status": payload.status}, commit=False,
    )
    if target is not None and payload.status == "REJECTED":
        create_notification(
            db, target.id, "Deactivation request declined",
            payload.decision_note or "Your request was declined.",
            notification_type="ACCOUNT", reference_type="deactivation_request",
            reference_id=row.id,
        )
    db.commit()
    db.refresh(row)
    return row
