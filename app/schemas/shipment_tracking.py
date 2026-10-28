
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ShipmentTrackingCreate(BaseModel):
    status: str = Field(min_length=1, max_length=50)
    location: str | None = Field(default=None, max_length=150)
    description: str = Field(min_length=3, max_length=1000)
    event_time: datetime | None = None


class ShipmentTrackingResponse(BaseModel):
    id: int
    shipping_order_id: int
    status: str
    location: str | None
    description: str
    event_time: datetime
    created_by: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
