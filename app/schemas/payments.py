
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    user_id: int
    provider: str
    provider_reference: str | None
    checkout_url: str | None
    amount: Decimal
    currency: str
    status: str
    created_at: datetime
    updated_at: datetime


class PaymentTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payment_id: int
    event_type: str
    provider_event_id: str | None
    status: str
    amount: Decimal
    currency: str
    message: str | None
    created_at: datetime
