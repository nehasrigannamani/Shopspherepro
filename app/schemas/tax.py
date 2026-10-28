from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class TaxRateCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    code: str = Field(min_length=2, max_length=50)
    rate_percent: Decimal = Field(ge=0, le=100, max_digits=7, decimal_places=4)
    category_id: int | None = Field(default=None, gt=0)
    country_code: str = Field(default="IN", min_length=2, max_length=2)
    region_code: str | None = Field(default=None, max_length=50)
    is_inclusive: bool = False
    is_active: bool = True


class TaxRateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    rate_percent: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=7, decimal_places=4
    )
    category_id: int | None = Field(default=None, gt=0)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    region_code: str | None = Field(default=None, max_length=50)
    is_inclusive: bool | None = None
    is_active: bool | None = None


class TaxRateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    rate_percent: Decimal
    category_id: int | None
    country_code: str
    region_code: str | None
    is_inclusive: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TaxCalculateRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    country_code: str = Field(default="IN", min_length=2, max_length=2)
    region_code: str | None = Field(default=None, max_length=50)
    category_id: int | None = Field(default=None, gt=0)


class TaxCalculateResponse(BaseModel):
    base_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    rate_percent: Decimal
    tax_name: str | None
    tax_inclusive: bool