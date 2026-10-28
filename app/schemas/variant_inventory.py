
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VariantInventoryCreate(BaseModel):
    variant_id: int = Field(gt=0)
    quantity_on_hand: int = Field(default=0, ge=0)
    low_stock_threshold: int = Field(default=5, ge=0)


class VariantInventoryAdjustment(BaseModel):
    quantity_change: int = Field(
        description="Positive to add stock, negative to remove stock"
    )
    reason: str = Field(min_length=3, max_length=255)

    @model_validator(mode="after")
    def validate_adjustment(self):
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("Adjustment reason cannot be empty")
        if self.quantity_change == 0:
            raise ValueError("Quantity change cannot be zero")
        return self


class VariantInventoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    variant_id: int
    quantity_on_hand: int
    reserved_quantity: int
    low_stock_threshold: int
    updated_at: datetime
    available_quantity: int


class VariantInventoryMovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    variant_inventory_id: int
    quantity_change: int
    reason: str
    performed_by: int | None
    created_at: datetime
