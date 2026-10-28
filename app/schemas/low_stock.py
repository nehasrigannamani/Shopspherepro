
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ThresholdUpdate(BaseModel):
    threshold: int = Field(ge=0)


class GlobalLowStockItem(BaseModel):
    inventory_id: int
    product_id: int
    product_name: str
    sku: str
    quantity_on_hand: int
    reserved_quantity: int
    available_quantity: int
    threshold: int


class WarehouseLowStockItem(BaseModel):
    warehouse_stock_id: int
    warehouse_id: int
    warehouse_name: str
    product_id: int
    product_name: str
    sku: str
    quantity_on_hand: int
    threshold: int


class LowStockThresholdHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    inventory_id: int | None
    warehouse_stock_id: int | None
    old_threshold: int | None
    new_threshold: int
    changed_by: int | None
    created_at: datetime


class ThresholdHistoryPage(BaseModel):
    items: list[LowStockThresholdHistoryResponse]
    total: int
    offset: int
    limit: int
