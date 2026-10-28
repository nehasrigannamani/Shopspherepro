from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CouponCreate(BaseModel):
    code: str = Field(min_length=3, max_length=50)
    description: str | None = Field(default=None, max_length=255)
    discount_type: Literal["PERCENTAGE", "FIXED"]
    discount_value: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    min_order_amount: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        max_digits=12,
        decimal_places=2,
    )
    usage_limit: int | None = Field(default=None, gt=0)
    starts_at: datetime
    expires_at: datetime
    is_active: bool = True

    @model_validator(mode="after")
    def validate_coupon(self):
        if self.discount_type == "PERCENTAGE" and self.discount_value > 100:
            raise ValueError("Percentage discount cannot exceed 100")
        if self.expires_at <= self.starts_at:
            raise ValueError("expires_at must be later than starts_at")
        self.code = self.code.strip().upper()
        if not self.code:
            raise ValueError("Coupon code cannot be empty")
        return self


class CouponUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=255)
    discount_value: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=2,
    )
    min_order_amount: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=12,
        decimal_places=2,
    )
    usage_limit: int | None = Field(default=None, gt=0)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    is_active: bool | None = None


class CouponResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    description: str | None
    discount_type: str
    discount_value: Decimal
    min_order_amount: Decimal
    usage_limit: int | None
    used_count: int
    starts_at: datetime
    expires_at: datetime
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CouponValidateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    order_subtotal: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class CouponValidateResponse(BaseModel):
    code: str
    discount_type: str
    discount_value: Decimal
    order_subtotal: Decimal
    discount_amount: Decimal
    payable_subtotal: Decimal