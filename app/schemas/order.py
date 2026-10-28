from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CheckoutRequest(BaseModel):
    shipping_method_id: int = Field(gt=0)
    coupon_code: str | None = Field(default=None, max_length=50)

    # Either reference saved addresses...
    shipping_address_id: int | None = Field(default=None, gt=0)
    billing_address_id: int | None = Field(default=None, gt=0)

    # ...or supply the shipping address inline.
    shipping_full_name: str | None = Field(default=None, min_length=2, max_length=150)
    shipping_phone: str | None = Field(default=None, min_length=5, max_length=30)
    shipping_address_line1: str | None = Field(default=None, min_length=3, max_length=255)
    shipping_address_line2: str | None = Field(default=None, max_length=255)
    shipping_city: str | None = Field(default=None, min_length=2, max_length=100)
    shipping_state: str | None = Field(default=None, min_length=2, max_length=100)
    shipping_postal_code: str | None = Field(default=None, min_length=3, max_length=20)
    shipping_country: str = Field(default="IN", min_length=2, max_length=2)
    notes: str | None = None

    @model_validator(mode="after")
    def require_shipping_address(self):
        if self.shipping_address_id is None:
            required = {
                "shipping_full_name": self.shipping_full_name,
                "shipping_phone": self.shipping_phone,
                "shipping_address_line1": self.shipping_address_line1,
                "shipping_city": self.shipping_city,
                "shipping_state": self.shipping_state,
                "shipping_postal_code": self.shipping_postal_code,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(
                    "Provide shipping_address_id or all of: " + ", ".join(missing)
                )
        return self


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    variant_id: int | None = None
    variant_sku: str | None = None
    variant_options: dict | None = None
    product_name: str
    product_sku: str
    product_description: str | None = None
    fulfillment_status: str = "PENDING"
    quantity: int
    unit_price: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    line_total: Decimal


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    user_id: int
    status: str
    payment_status: str
    payment_method: str | None = None
    currency: str
    subtotal: Decimal
    discount_total: Decimal
    shipping_total: Decimal
    tax_total: Decimal
    grand_total: Decimal
    coupon_code: str | None
    shipping_method_name: str
    created_at: datetime


class OrderDetailResponse(OrderResponse):
    shipping_address_id: int | None = None
    billing_address_id: int | None = None
    shipping_full_name: str
    shipping_phone: str
    shipping_address_line1: str
    shipping_address_line2: str | None
    shipping_city: str
    shipping_state: str
    shipping_postal_code: str
    shipping_country: str
    notes: str | None
    items: list[OrderItemResponse]