import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import as_utc, decode_token, utcnow
from app.db.session import get_db
from app.models.auth import AuthSession
from app.models.user import User, UserRole


bearer_scheme = HTTPBearer()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Validate an access token, its login session and its active user."""
    try:
        payload = decode_token(credentials.credentials, "access")
        user_id = int(payload["sub"])
        session_id = payload.get("sid")
        if not session_id:
            raise _unauthorized()
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        raise _unauthorized() from None

    session = db.get(AuthSession, session_id)
    if (
        session is None
        or session.user_id != user_id
        or session.revoked_at is not None
        or as_utc(session.expires_at) <= utcnow()
    ):
        # Logged-out / revoked sessions invalidate their access tokens.
        raise _unauthorized()

    user = db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise _unauthorized()

    return user


def require_roles(*allowed_roles: UserRole | str):
    """Allow access only to users whose roles are explicitly permitted."""
    allowed = {
        role if isinstance(role, UserRole) else UserRole(role)
        for role in allowed_roles
    }

    def role_checker(
        current_user: User = Depends(get_current_user),
    ) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource",
            )
        return current_user

    return role_checker
