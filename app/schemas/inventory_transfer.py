
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TransferStatus = Literal[
    "PENDING", "DISPATCHED", "RECEIVED", "CANCELLED"
]


class InventoryTransferCreate(BaseModel):
    source_warehouse_id: int = Field(gt=0)
    destination_warehouse_id: int = Field(gt=0)
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    reason: str = Field(min_length=3, max_length=2000)

    @model_validator(mode="after")
    def validate_warehouses(self):
        if self.source_warehouse_id == self.destination_warehouse_id:
            raise ValueError(
                "Source and destination warehouses must be different."
            )
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("Reason cannot be empty.")
        return self


class InventoryTransferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_warehouse_id: int
    destination_warehouse_id: int
    product_id: int
    quantity: int
    reason: str
    status: TransferStatus
    created_by: int | None
    dispatched_by: int | None
    received_by: int | None
    created_at: datetime
    dispatched_at: datetime | None
    received_at: datetime | None
    updated_at: datetime


class InventoryTransferListResponse(BaseModel):
    items: list[InventoryTransferResponse]
    total: int
    offset: int
    limit: int
