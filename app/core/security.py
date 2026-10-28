import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import settings


password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Convert a plain-text password into a secure password hash."""
    return password_hasher.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Check a supplied password against its stored hash."""
    return password_hasher.verify(password, hashed_password)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Treat naive datetimes coming back from MySQL as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def create_access_token(user_id: int, session_id: str | None = None) -> str:
    """Create a signed JWT access token bound to a login session."""
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "type": "access",
        "sid": session_id,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_refresh_token(
    user_id: int, session_id: str
) -> tuple[str, str, datetime]:
    """Return (token, jti, expires_at) for a refresh token."""
    now = utcnow()
    expires_at = now + timedelta(days=settings.refresh_token_expire_days)
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "sid": session_id,
        "jti": jti,
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(
        payload, settings.secret_key, algorithm=settings.algorithm
    )
    return token, jti, expires_at


def decode_token(token: str, expected_type: str) -> dict:
    """Decode a JWT and verify its type. Raises jwt.InvalidTokenError."""
    payload = jwt.decode(
        token, settings.secret_key, algorithms=[settings.algorithm]
    )
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("Wrong token type")
    return payload


def generate_opaque_token() -> str:
    """URL-safe random token for email verification links."""
    return secrets.token_urlsafe(32)


def hash_opaque_token(token: str) -> str:
    """Store only a hash of opaque tokens in the database."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
