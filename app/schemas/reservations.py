from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReservationCreate(BaseModel):
    ttl_minutes: int | None = Field(
        default=None, ge=1, le=1440,
        description="Hold duration; defaults to RESERVATION_TTL_MINUTES and is capped by RESERVATION_MAX_TTL_MINUTES.",
    )


class ReservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    group_id: str
    user_id: int
    product_id: int
    variant_id: int | None
    quantity: int
    status: str
    release_reason: str | None
    order_id: int | None
    expires_at: datetime
    released_at: datetime | None
    created_at: datetime


class ReservationGroupResponse(BaseModel):
    expires_at: datetime
    reservations: list[ReservationResponse]


class ReservationPage(BaseModel):
    items: list[ReservationResponse]
    total: int
    page: int
    page_size: int


class ExpiryRunResponse(BaseModel):
    released: int
