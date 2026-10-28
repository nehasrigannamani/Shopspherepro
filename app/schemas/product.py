
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductCreate(BaseModel):
    sku: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    category: str | None = Field(default=None, min_length=2, max_length=100)
    category_id: int | None = Field(default=None, gt=0)
    brand_id: int | None = Field(default=None, gt=0)
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    compare_at_price: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    image_url: str | None = Field(default=None, max_length=2048)

    @field_validator("sku")
    @classmethod
    def normalize_sku(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("name", "category")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=2, max_length=64)
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    category: str | None = Field(default=None, min_length=2, max_length=100)
    category_id: int | None = Field(default=None, gt=0)
    brand_id: int | None = Field(default=None, gt=0)
    price: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    compare_at_price: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    image_url: str | None = Field(default=None, max_length=2048)
    is_active: bool | None = None

    @field_validator("sku")
    @classmethod
    def normalize_sku(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None

    @field_validator("name", "category")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    description: str | None
    category: str
    category_id: int | None
    brand_id: int | None
    price: Decimal
    compare_at_price: Decimal | None
    image_url: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
