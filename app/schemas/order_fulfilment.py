from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LineIn(BaseModel):
    order_item_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=10000)


class ShipmentCreate(BaseModel):
    carrier_name: str = Field(min_length=2, max_length=100)
    tracking_number: str | None = Field(default=None, max_length=100)
    estimated_delivery_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=1000)
    items: list[LineIn] = Field(min_length=1, max_length=100)

    @field_validator("carrier_name", "tracking_number", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


class BackorderCreate(BaseModel):
    quantity: int = Field(ge=1, le=10000)
    expected_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=500)


class CancelItemsRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    items: list[LineIn] = Field(min_length=1, max_length=100)


class StatusChange(BaseModel):
    status: str = Field(min_length=3, max_length=30)
    reason: str = Field(min_length=3, max_length=1000)


class OrderAdminUpdate(BaseModel):
    """Only the fields sent are changed. A reason is mandatory (it is audited)."""

    reason: str = Field(min_length=3, max_length=1000)
    notes: str | None = Field(default=None, max_length=2000)
    shipping_full_name: str | None = Field(default=None, min_length=1, max_length=150)
    shipping_phone: str | None = Field(default=None, min_length=5, max_length=30)
    shipping_address_line1: str | None = Field(default=None, min_length=1, max_length=255)
    shipping_address_line2: str | None = Field(default=None, max_length=255)
    shipping_city: str | None = Field(default=None, min_length=1, max_length=100)
    shipping_state: str | None = Field(default=None, min_length=1, max_length=100)
    shipping_postal_code: str | None = Field(default=None, min_length=2, max_length=20)
    shipping_country: str | None = Field(default=None, min_length=2, max_length=2)


class ShipmentItemOut(BaseModel):
    order_item_id: int
    quantity: int


class ShipmentOut(BaseModel):
    id: int
    order_id: int
    shipment_number: str
    carrier_name: str
    tracking_number: str | None
    status: str
    estimated_delivery_at: datetime | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    notes: str | None
    items: list[ShipmentItemOut]


class BackorderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    order_item_id: int
    quantity: int
    status: str
    expected_at: datetime | None
    reason: str | None
    created_at: datetime
    resolved_at: datetime | None


class FulfilmentItem(BaseModel):
    order_item_id: int
    product_id: int
    product_name: str
    sku: str
    ordered: int
    cancelled: int
    shipped: int
    delivered: int
    backordered: int
    shippable_now: int
    fulfillment_status: str


class FulfilmentTotals(BaseModel):
    ordered: int
    cancelled: int
    shipped: int
    delivered: int
    backordered: int
    remaining_to_ship: int


class FulfilmentResponse(BaseModel):
    order_id: int
    order_number: str
    status: str
    payment_status: str
    totals: FulfilmentTotals
    items: list[FulfilmentItem]
    shipments: list[ShipmentOut]
    backorders: list[BackorderOut]


class TimelineEvent(BaseModel):
    at: datetime | None
    type: str
    title: str
    detail: str | None = None
    actor_id: int | None = None


class CancelledLine(BaseModel):
    order_item_id: int
    quantity: int
    refund_amount: Decimal


class CancelItemsResponse(BaseModel):
    order_id: int
    order_status: str
    cancelled: list[CancelledLine]
    refund_id: int | None
    refund_status: str | None
    refund_note: str | None


class OrderAdminUpdateResponse(BaseModel):
    order_id: int
    changed_fields: list[str]
