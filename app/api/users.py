from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.core.permissions import ROLE_PERMISSIONS
from app.core.security import hash_password
from app.db.session import get_db
from app.models.account import UserActivity
from app.models.user import User, UserRole
from app.schemas.users import (
    ActivityResponse,
    RoleAssignment,
    UserAdminCreate,
    UserAdminUpdate,
    UserDetail,
    UserList,
)
from app.services.accounts import record_activity, revoke_all_sessions
from app.services.audit import write_audit_log

router = APIRouter(prefix="/users", tags=["User Management"])

MANAGERS = (UserRole.SUPER_ADMIN, UserRole.ADMIN)
PRIVILEGED_ROLES = {UserRole.SUPER_ADMIN, UserRole.ADMIN}


def ensure_can_manage(actor: User, target: User) -> None:
    """Only a SUPER_ADMIN may manage admin-level accounts."""
    if actor.role == UserRole.SUPER_ADMIN:
        return
    if target.role in PRIVILEGED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a super admin can manage admin accounts",
        )


def ensure_can_grant(actor: User, role: UserRole) -> None:
    if role in PRIVILEGED_ROLES and actor.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a super admin can assign admin-level roles",
        )


def get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/roles/permissions")
def list_role_permissions(
    current_user: User = Depends(require_roles(*MANAGERS)),
):
    """The role → permission matrix enforced by the API."""
    return {role.value: perms for role, perms in ROLE_PERMISSIONS.items()}


@router.post("", response_model=UserDetail, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserAdminCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGERS)),
):
    ensure_can_grant(current_user, payload.role)
    email = str(payload.email).lower()

    conditions = [User.email == email]
    if payload.phone:
        conditions.append(User.phone == payload.phone)
    if db.query(User.id).filter(or_(*conditions)).first():
        raise HTTPException(
            status_code=409,
            detail="An account with this email or phone already exists",
        )

    user = User(
        full_name=payload.full_name.strip(),
        email=email,
        phone=payload.phone,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        is_active=True,
        is_verified=True,  # staff accounts are vouched for by an admin
    )
    db.add(user)
    try:
        db.flush()
        write_audit_log(
            db, actor_user_id=current_user.id, action="USER_CREATED",
            entity_type="user", entity_id=user.id,
            details={"role": payload.role.value}, commit=False,
        )
        record_activity(db, user.id, "ACCOUNT_CREATED_BY_ADMIN", f"by {current_user.id}")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Duplicate email or phone") from None
    db.refresh(user)
    return user


@router.get("", response_model=UserList)
def search_users(
    q: str | None = Query(default=None, min_length=1, max_length=100),
    role: UserRole | None = None,
    is_active: bool | None = None,
    is_verified: bool | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGERS)),
):
    query = db.query(User)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(User.full_name.like(like), User.email.like(like), User.phone.like(like))
        )
    if role is not None:
        query = query.filter(User.role == role)
    if is_active is not None:
        query = query.filter(User.is_active.is_(is_active))
    if is_verified is not None:
        query = query.filter(User.is_verified.is_(is_verified))

    total = query.with_entities(func.count(User.id)).scalar() or 0
    items = query.order_by(User.id.asc()).offset(skip).limit(limit).all()
    return UserList(total=total, items=items)


@router.get("/{user_id}", response_model=UserDetail)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGERS)),
):
    return get_user_or_404(db, user_id)


@router.patch("/{user_id}", response_model=UserDetail)
def update_user(
    user_id: int,
    payload: UserAdminUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGERS)),
):
    user = get_user_or_404(db, user_id)
    ensure_can_manage(current_user, user)

    changes = payload.model_dump(exclude_unset=True)
    if "phone" in changes and changes["phone"]:
        taken = db.query(User.id).filter(
            User.phone == changes["phone"], User.id != user.id
        ).first()
        if taken:
            raise HTTPException(status_code=409, detail="Phone already in use")

    for field, value in changes.items():
        setattr(user, field, value.strip() if isinstance(value, str) else value)

    write_audit_log(
        db, actor_user_id=current_user.id, action="USER_UPDATED",
        entity_type="user", entity_id=user.id,
        details={"fields": sorted(changes)}, commit=False,
    )
    db.commit()
    db.refresh(user)
    return user


@router.put("/{user_id}/role", response_model=UserDetail)
def assign_role(
    user_id: int,
    payload: RoleAssignment,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGERS)),
):
    user = get_user_or_404(db, user_id)
    if user.id == current_user.id:
        raise HTTPException(status_code=409, detail="You cannot change your own role")
    ensure_can_manage(current_user, user)
    ensure_can_grant(current_user, payload.role)

    previous = user.role
    user.role = payload.role
    # Existing tokens carry no role, but sessions are reset so a demoted
    # employee has to log in again under the new role.
    revoke_all_sessions(db, user.id, "role_changed")
    record_activity(db, user.id, "ROLE_CHANGED", f"{previous.value} -> {payload.role.value}")
    write_audit_log(
        db, actor_user_id=current_user.id, action="USER_ROLE_CHANGED",
        entity_type="user", entity_id=user.id,
        details={"from": previous.value, "to": payload.role.value}, commit=False,
    )
    db.commit()
    db.refresh(user)
    return user


def _set_active(db, current_user, user_id, active: bool) -> User:
    user = get_user_or_404(db, user_id)
    if user.id == current_user.id:
        raise HTTPException(status_code=409, detail="You cannot change your own account status")
    ensure_can_manage(current_user, user)

    user.is_active = active
    if not active:
        revoke_all_sessions(db, user.id, "account_deactivated")
    record_activity(db, user.id, "ACTIVATED" if active else "DEACTIVATED", f"by {current_user.id}")
    write_audit_log(
        db, actor_user_id=current_user.id,
        action="USER_ACTIVATED" if active else "USER_DEACTIVATED",
        entity_type="user", entity_id=user.id, commit=False,
    )
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/deactivate", response_model=UserDetail)
def deactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGERS)),
):
    return _set_active(db, current_user, user_id, False)


@router.post("/{user_id}/activate", response_model=UserDetail)
def activate_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGERS)),
):
    return _set_active(db, current_user, user_id, True)


@router.get("/{user_id}/activity", response_model=list[ActivityResponse])
def get_user_activity(
    user_id: int,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGERS)),
):
    get_user_or_404(db, user_id)
    return (
        db.query(UserActivity)
        .filter(UserActivity.user_id == user_id)
        .order_by(UserActivity.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
