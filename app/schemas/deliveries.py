
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeliveryCreate(BaseModel):
    assigned_to: int = Field(gt=0)
    recipient_name: str | None = Field(
        default=None, min_length=2, max_length=150
    )
    delivery_notes: str | None = Field(
        default=None, max_length=2000
    )


class DeliveryStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)
    recipient_name: str | None = Field(
        default=None, min_length=2, max_length=150
    )
    delivery_notes: str | None = Field(
        default=None, max_length=2000
    )
    failure_reason: str | None = Field(
        default=None, min_length=3, max_length=500
    )


class DeliveryResponse(BaseModel):
    id: int
    shipping_order_id: int
    assigned_to: int
    status: str
    recipient_name: str | None
    delivery_notes: str | None
    failure_reason: str | None
    assigned_at: datetime
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
