
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


TicketSubject = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=5,
        max_length=200,
    ),
]

TicketDescription = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=10,
        max_length=10000,
    ),
]

TicketMessageText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=10000,
    ),
]


class SupportTicketCreate(BaseModel):
    order_id: int | None = Field(default=None, gt=0)
    subject: TicketSubject
    description: TicketDescription
    category: str = Field(
        default="GENERAL",
        pattern=r"^(GENERAL|ORDER|PAYMENT|DELIVERY|RETURN|REFUND|PRODUCT|ACCOUNT)$",
    )
    priority: str = Field(
        default="MEDIUM",
        pattern=r"^(LOW|MEDIUM|HIGH|URGENT)$",
    )


class SupportTicketUpdate(BaseModel):
    status: str | None = Field(
        default=None,
        pattern=r"^(OPEN|IN_PROGRESS|WAITING_FOR_CUSTOMER|RESOLVED|CLOSED)$",
    )
    priority: str | None = Field(
        default=None,
        pattern=r"^(LOW|MEDIUM|HIGH|URGENT)$",
    )
    assigned_to: int | None = Field(default=None, gt=0)
    resolution_note: str | None = Field(default=None, max_length=5000)


class SupportTicketMessageCreate(BaseModel):
    message: TicketMessageText


class SupportTicketResponse(BaseModel):
    id: int
    ticket_number: str
    user_id: int
    order_id: int | None
    subject: str
    description: str
    category: str
    priority: str
    status: str
    assigned_to: int | None
    resolution_note: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SupportTicketMessageResponse(BaseModel):
    id: int
    ticket_id: int
    sender_id: int
    message: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
