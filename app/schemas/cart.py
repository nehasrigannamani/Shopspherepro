
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CartItemAdd(BaseModel):
    product_id: int = Field(gt=0)
    variant_id: int | None = Field(default=None, gt=0)
    quantity: int = Field(default=1, ge=1, le=100)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=100)


class CartItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    variant_id: int | None = None
    variant_sku: str | None = None
    variant_options: dict | None = None
    sku: str
    name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal


class CartResponse(BaseModel):
    id: int
    user_id: int
    items: list[CartItemResponse]
    total_items: int
    subtotal: Decimal
    created_at: datetime
    updated_at: datetime
