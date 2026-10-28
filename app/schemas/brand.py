
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class BrandCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = None
    website_url: HttpUrl | None = None


class BrandUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = None
    website_url: HttpUrl | None = None
    is_active: bool | None = None


class BrandResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    website_url: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
