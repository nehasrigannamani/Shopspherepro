
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReturnRequestCreate(BaseModel):
    order_id: int = Field(gt=0)
    order_item_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    reason: str = Field(min_length=5, max_length=2000)


class ReturnDecisionUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)
    decision_note: str | None = Field(
        default=None, max_length=2000
    )


class ReturnStatusUpdate(BaseModel):
    status: str = Field(min_length=1, max_length=30)
    decision_note: str | None = Field(
        default=None, max_length=2000
    )


class ReturnResponse(BaseModel):
    id: int
    order_id: int
    order_item_id: int
    user_id: int
    quantity: int
    reason: str
    status: str
    decision_note: str | None
    processed_by: int | None
    requested_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
