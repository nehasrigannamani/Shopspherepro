
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SupplierInvoiceCreate(BaseModel):
    invoice_number: str = Field(min_length=1, max_length=100)
    supplier_invoice_number: str | None = Field(
        default=None, max_length=100
    )
    supplier_id: int = Field(gt=0)
    purchase_order_id: int | None = Field(default=None, gt=0)
    invoice_date: date
    due_date: date | None = None
    currency: str = Field(default="INR", min_length=3, max_length=3)
    subtotal: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    tax_amount: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        max_digits=12,
        decimal_places=2,
    )
    notes: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.due_date and self.due_date < self.invoice_date:
            raise ValueError(
                "Due date cannot be earlier than invoice date."
            )
        return self


class SupplierInvoiceStatusUpdate(BaseModel):
    status: Literal["APPROVED", "PAID", "VOID"]


class SupplierInvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str
    supplier_invoice_number: str | None
    supplier_id: int
    purchase_order_id: int | None
    invoice_date: date
    due_date: date | None
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    status: str
    notes: str | None
    created_by: int
    paid_at: datetime | None
    created_at: datetime
    updated_at: datetime
