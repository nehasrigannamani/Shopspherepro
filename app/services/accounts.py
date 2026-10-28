"""Shared helpers for sessions, activity tracking and email verification."""
import uuid
from datetime import timedelta

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    generate_opaque_token,
    hash_opaque_token,
    utcnow,
)
from app.models.account import UserActivity
from app.models.auth import AuthSession, EmailVerificationToken, RefreshToken
from app.models.user import User
from app.services.audit import request_ip
from app.services.notify import queue_delivery


def record_activity(
    db: Session,
    user_id: int,
    action: str,
    detail: str | None = None,
    ip: str | None = None,
) -> None:
    db.add(
        UserActivity(
            user_id=user_id,
            action=action,
            detail=(detail or "")[:255] or None,
            ip_address=ip,
        )
    )


def start_session(db: Session, user: User, request: Request | None):
    """Create a login session plus its first access/refresh token pair."""
    session = AuthSession(
        id=uuid.uuid4().hex,
        user_id=user.id,
        user_agent=(
            request.headers.get("user-agent", "")[:255] if request else None
        ),
        ip_address=request_ip(request) if request else None,
        expires_at=utcnow() + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(session)
    db.flush()
    return session, issue_tokens(db, user, session)


def issue_tokens(db: Session, user: User, session: AuthSession) -> dict:
    refresh, jti, refresh_expires = create_refresh_token(user.id, session.id)
    db.add(
        RefreshToken(
            jti=jti, session_id=session.id, expires_at=refresh_expires
        )
    )
    return {
        "access_token": create_access_token(user.id, session.id),
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
        "refresh_expires_in": settings.refresh_token_expire_days * 86400,
    }


def revoke_session(db: Session, session: AuthSession, reason: str) -> None:
    if session.revoked_at is None:
        session.revoked_at = utcnow()
        session.revoked_reason = reason


def revoke_all_sessions(
    db: Session, user_id: int, reason: str, except_session: str | None = None
) -> int:
    count = 0
    sessions = (
        db.query(AuthSession)
        .filter(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .all()
    )
    for session in sessions:
        if session.id != except_session:
            revoke_session(db, session, reason)
            count += 1
    return count


def send_verification_email(db: Session, user: User) -> str:
    """Create a verification token and queue the email. Returns the raw token."""
    token = generate_opaque_token()
    db.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=hash_opaque_token(token),
            expires_at=utcnow()
            + timedelta(hours=settings.email_verification_expire_hours),
        )
    )
    link = f"{settings.public_base_url}/auth/verify-email?token={token}"
    queue_delivery(
        db,
        user_id=user.id,
        channel="EMAIL",
        recipient=user.email,
        subject="Verify your ShopSphere email address",
        message=(
            f"Hello {user.full_name},\n\n"
            "Please verify your email address by submitting this token to "
            f"POST /auth/verify-email, or open:\n{link}\n\n"
            f"The token is valid for {settings.email_verification_expire_hours} hours."
        ),
    )
    return token
