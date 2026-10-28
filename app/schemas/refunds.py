
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RefundRequest(BaseModel):
    payment_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reason: str = Field(min_length=3, max_length=1000)


class RefundDecisionRequest(BaseModel):
    status: str
    decision_note: str | None = Field(default=None, max_length=1000)


class RefundResponse(BaseModel):
    id: int
    payment_id: int
    order_id: int
    requested_by: int
    processed_by: int | None
    amount: Decimal
    currency: str
    reason: str
    status: str
    decision_note: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
