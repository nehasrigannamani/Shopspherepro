from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import UserRole
from app.schemas.auth import validate_password_strength, validate_phone


class UserAdminCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=20)
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.CUSTOMER

    _phone = field_validator("phone")(validate_phone)
    _password = field_validator("password")(validate_password_strength)


class UserAdminUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=150)
    phone: str | None = Field(default=None, max_length=20)
    is_verified: bool | None = None

    _phone = field_validator("phone")(validate_phone)


class RoleAssignment(BaseModel):
    role: UserRole


class UserDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: EmailStr
    phone: str | None
    role: UserRole
    is_active: bool
    is_verified: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UserList(BaseModel):
    total: int
    items: list[UserDetail]


class ActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    action: str
    detail: str | None
    ip_address: str | None
    created_at: datetime


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=150)
    phone: str | None = Field(default=None, max_length=20)

    _phone = field_validator("phone")(validate_phone)


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: EmailStr
    phone: str | None
    role: UserRole
    is_verified: bool
    last_login_at: datetime | None
    created_at: datetime


class ProfileHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    changed_by: int | None
    field_name: str
    old_value: str | None
    new_value: str | None
    created_at: datetime


class DeactivationRequestCreate(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class DeactivationDecision(BaseModel):
    status: str = Field(pattern=r"^(APPROVED|REJECTED)$")
    decision_note: str | None = Field(default=None, max_length=1000)


class DeactivationRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    reason: str | None
    status: str
    decided_by: int | None
    decision_note: str | None
    created_at: datetime
    decided_at: datetime | None


class AddressBase(BaseModel):
    address_type: str = Field(default="SHIPPING", pattern=r"^(SHIPPING|BILLING|BOTH)$")
    label: str | None = Field(default=None, max_length=50)
    full_name: str = Field(min_length=2, max_length=150)
    phone: str = Field(min_length=5, max_length=30, pattern=r"^[0-9+()\-\s]+$")
    line1: str = Field(min_length=3, max_length=255)
    line2: str | None = Field(default=None, max_length=255)
    city: str = Field(min_length=2, max_length=100)
    state: str = Field(min_length=2, max_length=100)
    postal_code: str = Field(
        min_length=3, max_length=20, pattern=r"^[A-Za-z0-9][A-Za-z0-9\s\-]*$"
    )
    country: str = Field(default="IN", min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")

    @field_validator("country")
    @classmethod
    def upper_country(cls, value: str) -> str:
        return value.upper()


class AddressCreate(AddressBase):
    is_default: bool = False


class AddressUpdate(BaseModel):
    address_type: str | None = Field(default=None, pattern=r"^(SHIPPING|BILLING|BOTH)$")
    label: str | None = Field(default=None, max_length=50)
    full_name: str | None = Field(default=None, min_length=2, max_length=150)
    phone: str | None = Field(default=None, min_length=5, max_length=30, pattern=r"^[0-9+()\-\s]+$")
    line1: str | None = Field(default=None, min_length=3, max_length=255)
    line2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, min_length=2, max_length=100)
    state: str | None = Field(default=None, min_length=2, max_length=100)
    postal_code: str | None = Field(
        default=None, min_length=3, max_length=20, pattern=r"^[A-Za-z0-9][A-Za-z0-9\s\-]*$"
    )
    country: str | None = Field(default=None, min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")

    @field_validator("country")
    @classmethod
    def upper_country(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class AddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    address_type: str
    label: str | None
    full_name: str
    phone: str
    line1: str
    line2: str | None
    city: str
    state: str
    postal_code: str
    country: str
    is_default_shipping: bool
    is_default_billing: bool
    created_at: datetime
    updated_at: datetime
