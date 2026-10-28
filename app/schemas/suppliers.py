
from datetime import datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
)


SupplierName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=200),
]

PhoneNumber = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=7, max_length=30),
]

AddressText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=255),
]


class SupplierCreate(BaseModel):
    name: SupplierName
    contact_person: str | None = Field(default=None, max_length=150)
    email: EmailStr
    phone: PhoneNumber

    address_line1: AddressText
    address_line2: str | None = Field(default=None, max_length=255)

    city: str = Field(min_length=2, max_length=100)
    state: str = Field(min_length=2, max_length=100)
    postal_code: str = Field(min_length=2, max_length=20)
    country: str = Field(min_length=2, max_length=100)

    tax_identifier: str | None = Field(default=None, max_length=100)
    payment_terms: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=5000)


class SupplierUpdate(BaseModel):
    name: SupplierName | None = None
    contact_person: str | None = Field(default=None, max_length=150)
    email: EmailStr | None = None
    phone: PhoneNumber | None = None

    address_line1: AddressText | None = None
    address_line2: str | None = Field(default=None, max_length=255)

    city: str | None = Field(default=None, min_length=2, max_length=100)
    state: str | None = Field(default=None, min_length=2, max_length=100)
    postal_code: str | None = Field(default=None, min_length=2, max_length=20)
    country: str | None = Field(default=None, min_length=2, max_length=100)

    tax_identifier: str | None = Field(default=None, max_length=100)
    payment_terms: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=5000)


class SupplierStatusUpdate(BaseModel):
    is_active: bool


class SupplierResponse(BaseModel):
    id: int
    name: str
    contact_person: str | None
    email: str
    phone: str

    address_line1: str
    address_line2: str | None
    city: str
    state: str
    postal_code: str
    country: str

    tax_identifier: str | None
    payment_terms: str | None
    notes: str | None
    is_active: bool

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
