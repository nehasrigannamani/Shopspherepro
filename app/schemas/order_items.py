
from datetime import datetime
from decimal import Decimal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    product_id: int
    product_name: str
    sku: str = Field(validation_alias=AliasChoices("sku", "product_sku"))
    variant_id: int | None = None
    variant_sku: str | None = None
    variant_options: dict | None = None
    product_description: str | None = None
    fulfillment_status: str = "PENDING"
    quantity: int
    unit_price: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    line_total: Decimal


class OrderStatusHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    previous_status: str | None
    new_status: str
    changed_by: int | None
    note: str | None
    created_at: datetime
