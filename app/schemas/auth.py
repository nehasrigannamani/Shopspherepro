import re

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)

from app.models.user import UserRole

PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


def validate_phone(value: str | None) -> str | None:
    """Accept international E.164 numbers such as +919876543210."""
    if value is None:
        return None
    cleaned = re.sub(r"[\s\-()]", "", value.strip())
    if not cleaned:
        return None
    if not PHONE_PATTERN.match(cleaned):
        raise ValueError(
            "Phone must be in international format, e.g. +919876543210"
        )
    return cleaned


def validate_password_strength(value: str) -> str:
    if not (
        re.search(r"[A-Za-z]", value) and re.search(r"\d", value)
    ):
        raise ValueError("Password must contain letters and digits")
    return value


class UserRegister(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=20)
    password: str = Field(min_length=8, max_length=128)

    _phone = field_validator("phone")(validate_phone)
    _password = field_validator("password")(validate_password_strength)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    _password = field_validator("new_password")(validate_password_strength)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: EmailStr
    phone: str | None
    role: UserRole
    is_active: bool
    is_verified: bool


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int
