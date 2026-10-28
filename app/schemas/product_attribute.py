
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


ALLOWED_DATA_TYPES = {"text", "integer", "decimal", "boolean"}


class ProductAttributeCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    data_type: str = Field(default="text", min_length=3, max_length=20)
    category_id: int | None = Field(default=None, gt=0)
    is_required: bool = False
    is_filterable: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Attribute name cannot be empty")
        return value

    @field_validator("data_type")
    @classmethod
    def validate_data_type(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in ALLOWED_DATA_TYPES:
            raise ValueError(
                "data_type must be text, integer, decimal, or boolean"
            )
        return value


class ProductAttributeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    data_type: str | None = Field(default=None, min_length=3, max_length=20)
    category_id: int | None = Field(default=None, gt=0)
    is_required: bool | None = None
    is_filterable: bool | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("data_type")
    @classmethod
    def validate_data_type(cls, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.strip().lower()
        if value not in ALLOWED_DATA_TYPES:
            raise ValueError(
                "data_type must be text, integer, decimal, or boolean"
            )
        return value


class ProductAttributeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    data_type: str
    category_id: int | None
    is_required: bool
    is_filterable: bool
    is_active: bool
    created_at: datetime


class ProductAttributeValueSet(BaseModel):
    value: str = Field(min_length=1, max_length=2000)

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Attribute value cannot be empty")
        return value


class ProductAttributeValueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    attribute_id: int
    value: str
    created_at: datetime
    updated_at: datetime
