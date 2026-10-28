import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import bearer_scheme, get_current_user
from app.core.security import (
    as_utc,
    decode_token,
    hash_opaque_token,
    hash_password,
    utcnow,
    verify_password,
)
from app.db.session import get_db
from app.models.auth import AuthSession, EmailVerificationToken, RefreshToken
from app.models.user import User, UserRole
from app.schemas.auth import (
    ChangePasswordRequest,
    RefreshRequest,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
    VerifyEmailRequest,
)
from app.services.accounts import (
    issue_tokens,
    record_activity,
    revoke_all_sessions,
    revoke_session,
    send_verification_email,
    start_session,
)
from app.services.audit import request_ip

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Verified against when the account does not exist so response time does not
# reveal which emails are registered.
_DUMMY_HASH = hash_password("not-a-real-password-1")


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: UserRegister,
    request: Request,
    db: Session = Depends(get_db),
):
    email = str(payload.email).lower()

    conditions = [User.email == email]
    if payload.phone:
        conditions.append(User.phone == payload.phone)

    if db.scalar(select(User).where(or_(*conditions))):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email or phone already exists",
        )

    user = User(
        full_name=payload.full_name.strip(),
        email=email,
        phone=payload.phone,
        hashed_password=hash_password(payload.password),
        role=UserRole.CUSTOMER,
        is_active=True,
        is_verified=False,
    )
    db.add(user)

    try:
        db.flush()
        send_verification_email(db, user)
        record_activity(db, user.id, "REGISTERED", ip=request_ip(request))
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email or phone already exists",
        ) from None

    return user


@router.post("/login", response_model=TokenResponse)
def login(
    payload: UserLogin,
    request: Request,
    db: Session = Depends(get_db),
):
    email = str(payload.email).lower()
    user = db.scalar(select(User).where(User.email == email))

    password_ok = verify_password(
        payload.password,
        user.hashed_password if user is not None else _DUMMY_HASH,
    )

    if user is None or not password_ok or not user.is_active:
        if user is not None:
            record_activity(
                db, user.id, "LOGIN_FAILED", ip=request_ip(request)
            )
            db.commit()
        raise _invalid_credentials()

    user.last_login_at = utcnow().replace(tzinfo=None)
    _, tokens = start_session(db, user, request)
    record_activity(db, user.id, "LOGIN", ip=request_ip(request))
    db.commit()

    return TokenResponse(**tokens)


@router.post("/refresh", response_model=TokenResponse)
def refresh_tokens(
    payload: RefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        claims = decode_token(payload.refresh_token, "refresh")
        user_id = int(claims["sub"])
        jti = claims["jti"]
        session_id = claims["sid"]
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        raise unauthorized from None

    token_row = db.scalar(
        select(RefreshToken).where(RefreshToken.jti == jti).with_for_update()
    )
    session = db.get(AuthSession, session_id)

    if (
        token_row is None
        or session is None
        or session.user_id != user_id
        or session.revoked_at is not None
        or as_utc(token_row.expires_at) <= utcnow()
    ):
        raise unauthorized

    if token_row.used_at is not None:
        # A spent refresh token came back: assume theft, kill the session.
        revoke_session(db, session, "refresh_token_reuse")
        record_activity(
            db, user_id, "REFRESH_REUSE_DETECTED", ip=request_ip(request)
        )
        db.commit()
        raise unauthorized

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized

    token_row.used_at = utcnow().replace(tzinfo=None)
    session.last_used_at = utcnow().replace(tzinfo=None)
    tokens = issue_tokens(db, user, session)
    db.commit()

    return TokenResponse(**tokens)


def _current_session_id(credentials: HTTPAuthorizationCredentials) -> str:
    claims = decode_token(credentials.credentials, "access")
    return claims["sid"]


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revoke the current session: its access and refresh tokens stop working."""
    session = db.get(AuthSession, _current_session_id(credentials))
    if session is not None:
        revoke_session(db, session, "logout")
    record_activity(db, current_user.id, "LOGOUT")
    db.commit()


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    revoke_all_sessions(db, current_user.id, "logout_all")
    record_activity(db, current_user.id, "LOGOUT_ALL")
    db.commit()


@router.get("/me", response_model=UserResponse)
def get_my_profile(
    current_user: User = Depends(get_current_user),
):
    return current_user


@router.post("/verify-email", response_model=UserResponse)
def verify_email(
    payload: VerifyEmailRequest,
    db: Session = Depends(get_db),
):
    token = db.scalar(
        select(EmailVerificationToken)
        .where(
            EmailVerificationToken.token_hash
            == hash_opaque_token(payload.token)
        )
        .with_for_update()
    )

    if (
        token is None
        or token.used_at is not None
        or as_utc(token.expires_at) <= utcnow()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token is invalid or has expired",
        )

    user = db.get(User, token.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Verification token is invalid")

    token.used_at = utcnow().replace(tzinfo=None)
    user.is_verified = True
    record_activity(db, user.id, "EMAIL_VERIFIED")
    db.commit()
    db.refresh(user)
    return user


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
def resend_verification(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email address is already verified",
        )
    send_verification_email(db, current_user)
    db.commit()
    return {"detail": "Verification email queued"}


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    current_user.hashed_password = hash_password(payload.new_password)
    # Other devices must log in again with the new password.
    revoke_all_sessions(
        db,
        current_user.id,
        "password_changed",
        except_session=_current_session_id(credentials),
    )
    record_activity(db, current_user.id, "PASSWORD_CHANGED")
    db.commit()
