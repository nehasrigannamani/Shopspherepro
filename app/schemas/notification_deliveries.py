
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints


MessageText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=10000,
    ),
]


class NotificationDeliveryCreate(BaseModel):
    user_id: int = Field(gt=0)
    notification_id: int | None = Field(default=None, gt=0)
    channel: str = Field(pattern=r"^(EMAIL|SMS)$")
    recipient: str = Field(min_length=3, max_length=255)
    subject: str | None = Field(default=None, max_length=255)
    message: MessageText


class EmailNotificationCreate(BaseModel):
    user_id: int = Field(gt=0)
    notification_id: int | None = Field(default=None, gt=0)
    recipient: EmailStr
    subject: str = Field(min_length=1, max_length=255)
    message: MessageText


class SMSNotificationCreate(BaseModel):
    user_id: int = Field(gt=0)
    notification_id: int | None = Field(default=None, gt=0)
    recipient: str = Field(
        pattern=r"^\+[1-9]\d{7,14}$",
        description="Recipient phone number in international E.164 format",
    )
    message: MessageText


class NotificationDeliveryResponse(BaseModel):
    id: int
    user_id: int
    notification_id: int | None
    channel: str
    recipient: str
    subject: str | None
    message: str
    status: str
    attempts: int
    provider_message_id: str | None
    error_message: str | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
