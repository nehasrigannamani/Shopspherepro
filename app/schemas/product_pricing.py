
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProductPriceUpdate(BaseModel):
    price: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    compare_at_price: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    reason: str = Field(min_length=3, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_price_fields(self):
        if self.price is None and self.compare_at_price is None:
            raise ValueError("Provide price or compare_at_price to update")

        if (
            self.price is not None
            and self.compare_at_price is not None
            and self.compare_at_price < self.price
        ):
            raise ValueError(
                "compare_at_price must be greater than or equal to price"
            )

        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("A reason for the price change is required")

        return self


class ProductPriceHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    old_price: Decimal
    new_price: Decimal
    old_compare_at_price: Decimal | None
    new_compare_at_price: Decimal | None
    changed_by: int | None
    reason: str
    notes: str | None
    created_at: datetime
