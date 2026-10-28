from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CartPriceLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    variant_id: int | None = None
    product_name: str
    sku: str
    quantity: int
    unit_price: Decimal
    line_subtotal: Decimal
    discount_amount: Decimal = Decimal("0.00")
    tax_amount: Decimal = Decimal("0.00")
    line_total: Decimal = Decimal("0.00")


class CartPriceSummary(BaseModel):
    cart_id: int
    currency: str = "INR"
    items: list[CartPriceLine]
    item_count: int
    subtotal: Decimal
    promotion_discount: Decimal = Decimal("0.00")
    coupon_discount: Decimal = Decimal("0.00")
    discount_total: Decimal
    shipping_total: Decimal
    tax_total: Decimal
    grand_total: Decimal
    coupon_code: str | None = None
    shipping_method: str | None = None
    pricing_note: str
