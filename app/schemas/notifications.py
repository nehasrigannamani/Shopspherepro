
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


NotificationTitle = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=2,
        max_length=200,
    ),
]

NotificationMessage = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=10000,
    ),
]


class NotificationCreate(BaseModel):
    user_id: int = Field(gt=0)
    title: NotificationTitle
    message: NotificationMessage
    notification_type: str = Field(
        default="GENERAL",
        pattern=r"^(GENERAL|ORDER|PAYMENT|SHIPPING|DELIVERY|RETURN|REFUND|SUPPORT|PROMOTION|ACCOUNT)$",
    )
    reference_type: str | None = Field(default=None, max_length=50)
    reference_id: int | None = Field(default=None, gt=0)


class NotificationResponse(BaseModel):
    id: int
    user_id: int
    title: str
    message: str
    notification_type: str
    reference_type: str | None
    reference_id: int | None
    is_read: bool
    read_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationBulkRead(BaseModel):
    notification_ids: list[int] = Field(min_length=1, max_length=100)
