
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AuditLogCreate(BaseModel):
    action: str = Field(min_length=1, max_length=100)
    entity_type: str = Field(min_length=1, max_length=100)
    entity_id: str | None = Field(default=None, max_length=100)
    outcome: str = Field(default="SUCCESS", max_length=20)
    details: str | None = Field(default=None, max_length=4000)
    request_id: str | None = Field(default=None, max_length=100)


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_user_id: int | None
    action: str
    entity_type: str
    entity_id: str | None
    outcome: str
    details: str | None
    ip_address: str | None
    request_id: str | None
    created_at: datetime
