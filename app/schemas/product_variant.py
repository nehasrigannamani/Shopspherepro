
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductVariantCreate(BaseModel):
    product_id: int = Field(gt=0)
    sku: str = Field(min_length=2, max_length=64)
    option_values: dict[str, Any] = Field(min_length=1)
    price: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )

    @field_validator("sku")
    @classmethod
    def normalize_sku(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("option_values")
    @classmethod
    def validate_options(cls, value: dict[str, Any]) -> dict[str, Any]:
        cleaned = {}

        for key, option_value in value.items():
            normalized_key = key.strip().lower()

            if not normalized_key:
                raise ValueError("Option names cannot be empty")

            if option_value is None or str(option_value).strip() == "":
                raise ValueError("Option values cannot be empty")

            cleaned[normalized_key] = option_value

        return cleaned


class ProductVariantUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=2, max_length=64)
    option_values: dict[str, Any] | None = None
    price: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    is_active: bool | None = None

    @field_validator("sku")
    @classmethod
    def normalize_sku(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None

    @field_validator("option_values")
    @classmethod
    def validate_options(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if value is None:
            return None

        cleaned = {}
        for key, option_value in value.items():
            normalized_key = key.strip().lower()

            if not normalized_key:
                raise ValueError("Option names cannot be empty")

            if option_value is None or str(option_value).strip() == "":
                raise ValueError("Option values cannot be empty")

            cleaned[normalized_key] = option_value

        if not cleaned:
            raise ValueError("At least one option is required")

        return cleaned


class ProductVariantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    sku: str
    option_values: dict[str, Any]
    price: Decimal | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
