from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ShippingMethodCreate(BaseModel):
    code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    base_fee: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    free_shipping_threshold: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    estimated_min_days: int = Field(default=2, ge=0)
    estimated_max_days: int = Field(default=5, ge=0)
    is_active: bool = True


class ShippingMethodUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    base_fee: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    free_shipping_threshold: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    estimated_min_days: int | None = Field(default=None, ge=0)
    estimated_max_days: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class ShippingMethodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    base_fee: Decimal
    free_shipping_threshold: Decimal | None
    estimated_min_days: int
    estimated_max_days: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ShippingCalculateRequest(BaseModel):
    method_id: int = Field(gt=0)
    order_subtotal: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class ShippingCalculateResponse(BaseModel):
    method_id: int
    method_name: str
    order_subtotal: Decimal
    shipping_fee: Decimal
    is_free_shipping: bool
    estimated_min_days: int
    estimated_max_days: int