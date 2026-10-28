
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ShippingOrderCreate(BaseModel):
    carrier_name: str = Field(min_length=2, max_length=100)
    tracking_number: str | None = Field(
        default=None, min_length=1, max_length=100
    )
    estimated_delivery_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ShippingOrderUpdate(BaseModel):
    carrier_name: str | None = Field(
        default=None, min_length=2, max_length=100
    )
    tracking_number: str | None = Field(
        default=None, min_length=1, max_length=100
    )
    estimated_delivery_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ShippingStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)
    notes: str | None = Field(default=None, max_length=2000)


class ShippingOrderResponse(BaseModel):
    id: int
    order_id: int
    carrier_name: str
    tracking_number: str | None
    status: str
    estimated_delivery_at: datetime | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    notes: str | None
    created_by: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
