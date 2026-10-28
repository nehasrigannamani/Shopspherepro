
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CategoryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(min_length=2, max_length=140)
    description: str | None = None
    parent_id: int | None = Field(default=None, gt=0)

    @field_validator("name", "slug")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip().lower() if value == value.lower() else value.strip()


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    slug: str | None = Field(default=None, min_length=2, max_length=140)
    description: str | None = None
    parent_id: int | None = Field(default=None, gt=0)
    is_active: bool | None = None


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str | None
    parent_id: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
