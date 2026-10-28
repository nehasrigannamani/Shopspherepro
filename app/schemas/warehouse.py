
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


WarehouseStatusValue = Literal["ACTIVE", "INACTIVE", "MAINTENANCE"]


class WarehouseCreate(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=2, max_length=150)
    description: str | None = None
    address: str = Field(min_length=3, max_length=255)
    city: str = Field(min_length=2, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str = Field(min_length=2, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    capacity_units: int | None = Field(default=None, ge=0)
    status: WarehouseStatusValue = "ACTIVE"

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("name", "address", "city", "country")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty.")
        return value


class WarehouseUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=2, max_length=40)
    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = None
    address: str | None = Field(default=None, min_length=3, max_length=255)
    city: str | None = Field(default=None, min_length=2, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, min_length=2, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    capacity_units: int | None = Field(default=None, ge=0)
    status: WarehouseStatusValue | None = None
    is_active: bool | None = None

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None


class WarehouseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    address: str
    city: str
    state: str | None
    country: str
    postal_code: str | None
    capacity_units: int | None
    status: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WarehouseStockCreate(BaseModel):
    product_id: int = Field(gt=0)
    quantity_on_hand: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=255)


class WarehouseStockAdjustment(BaseModel):
    quantity_change: int
    reason: str = Field(min_length=3, max_length=255)


class WarehouseStockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    warehouse_id: int
    product_id: int
    quantity_on_hand: int
    updated_at: datetime


class WarehouseStockMovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    warehouse_stock_id: int
    quantity_change: int
    reason: str
    performed_by: int | None
    created_at: datetime
