from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PromotionCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    description: str | None = None
    discount_type: Literal["PERCENTAGE", "FIXED"]
    discount_value: Decimal = Field(gt=0, max_digits=12, decimal_places=2)

    target_type: Literal["ALL", "PRODUCT", "CATEGORY"] = "ALL"
    product_id: int | None = Field(default=None, gt=0)
    category_id: int | None = Field(default=None, gt=0)

    starts_at: datetime
    expires_at: datetime
    usage_limit: int | None = Field(default=None, gt=0)
    priority: int = Field(default=1, ge=1)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_promotion(self):
        if self.discount_type == "PERCENTAGE" and self.discount_value > 100:
            raise ValueError("Percentage discount cannot exceed 100")

        if self.expires_at <= self.starts_at:
            raise ValueError("expires_at must be later than starts_at")

        if self.target_type == "PRODUCT":
            if self.product_id is None or self.category_id is not None:
                raise ValueError(
                    "PRODUCT targeting requires product_id only"
                )

        elif self.target_type == "CATEGORY":
            if self.category_id is None or self.product_id is not None:
                raise ValueError(
                    "CATEGORY targeting requires category_id only"
                )

        elif self.product_id is not None or self.category_id is not None:
            raise ValueError("ALL targeting cannot specify a product or category")

        return self


class PromotionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = None
    discount_value: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    usage_limit: int | None = Field(default=None, gt=0)
    priority: int | None = Field(default=None, ge=1)
    is_active: bool | None = None


class PromotionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    discount_type: str
    discount_value: Decimal
    target_type: str
    product_id: int | None
    category_id: int | None
    starts_at: datetime
    expires_at: datetime
    usage_limit: int | None
    used_count: int
    priority: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PromotionCalculateRequest(BaseModel):
    product_id: int = Field(gt=0)
    unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    quantity: int = Field(default=1, ge=1)


class PromotionCalculateResponse(BaseModel):
    product_id: int
    original_total: Decimal
    discount_amount: Decimal
    discounted_total: Decimal
    promotion_id: int | None
    promotion_name: str | None