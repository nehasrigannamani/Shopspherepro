
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InventoryCreate(BaseModel):
    product_id: int = Field(gt=0)
    quantity_on_hand: int = Field(ge=0)
    low_stock_threshold: int = Field(default=5, ge=0)


class InventoryAdjustment(BaseModel):
    quantity_change: int = Field(
        description="Positive to add stock, negative to remove stock"
    )
    reason: str = Field(min_length=3, max_length=255)

    @model_validator(mode="after")
    def validate_change(self):
        if self.quantity_change == 0:
            raise ValueError("quantity_change cannot be zero")
        if not self.reason.strip():
            raise ValueError("reason cannot be blank")
        return self


class InventoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    quantity_on_hand: int
    reserved_quantity: int
    low_stock_threshold: int
    updated_at: datetime
    available_quantity: int


class InventoryMovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    inventory_id: int
    quantity_change: int
    reason: str
    performed_by: int | None
    created_at: datetime
