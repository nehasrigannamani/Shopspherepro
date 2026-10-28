
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PurchaseOrderItemCreate(BaseModel):
    product_id: int = Field(gt=0)
    quantity_ordered: int = Field(gt=0)
    unit_cost: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class PurchaseOrderCreate(BaseModel):
    supplier_id: int = Field(gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    expected_delivery_date: date | None = None
    notes: str | None = Field(default=None, max_length=5000)
    items: list[PurchaseOrderItemCreate] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_products(self):
        product_ids = [item.product_id for item in self.items]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError(
                "A product can appear only once in a purchase order."
            )
        return self


class PurchaseOrderUpdate(BaseModel):
    expected_delivery_date: date | None = None
    notes: str | None = Field(default=None, max_length=5000)


class PurchaseOrderStatusUpdate(BaseModel):
    status: Literal[
        "SUBMITTED",
        "APPROVED",
        "ORDERED",
        "CANCELLED",
    ]


class PurchaseOrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    purchase_order_id: int
    product_id: int
    quantity_ordered: int
    quantity_received: int
    unit_cost: Decimal
    line_total: Decimal
    created_at: datetime
    updated_at: datetime


class PurchaseOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    po_number: str
    supplier_id: int
    status: str
    currency: str
    expected_delivery_date: date | None
    notes: str | None
    total_amount: Decimal
    created_by: int
    approved_by: int | None
    ordered_at: datetime | None
    received_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[PurchaseOrderItemResponse]


class PurchaseOrderItemReceive(BaseModel):
    quantity_received: int = Field(gt=0)
    notes: str | None = Field(default=None, max_length=1000)
