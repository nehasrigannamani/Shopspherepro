"""Server-side pricing engine shared by cart summary and checkout.

All amounts come from current database data; nothing is trusted from the
client. Pass ``lock=True`` inside a transaction to take row locks on the
rows that are about to change (stock, coupon, promotions).
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.cart import CartItem
from app.models.coupon import Coupon
from app.models.inventory import Inventory
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.promotion import Promotion
from app.models.shipping import ShippingMethod
from app.models.tax import TaxRate
from app.models.variant_inventory import VariantInventory

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def utc_value(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class PricedLine:
    cart_item: CartItem
    product: Product
    variant: ProductVariant | None
    stock: Inventory | VariantInventory
    unit_price: Decimal
    line_subtotal: Decimal
    promotion: Promotion | None = None
    promotion_discount: Decimal = ZERO
    coupon_share: Decimal = ZERO
    discount: Decimal = ZERO
    tax_base: Decimal = ZERO
    tax: Decimal = ZERO
    line_total: Decimal = ZERO


@dataclass
class PricingResult:
    lines: list[PricedLine] = field(default_factory=list)
    subtotal: Decimal = ZERO
    promotion_discount_total: Decimal = ZERO
    coupon: Coupon | None = None
    coupon_discount: Decimal = ZERO
    discount_total: Decimal = ZERO
    shipping_method: ShippingMethod | None = None
    shipping_total: Decimal = ZERO
    tax_total: Decimal = ZERO
    grand_total: Decimal = ZERO
    item_count: int = 0
    currency: str = "INR"


def calculate_tax(
    db: Session,
    amount: Decimal,
    product: Product,
    country_code: str,
    region_code: str | None,
) -> tuple[Decimal, Decimal]:
    """Return (taxable_base, tax_amount) for one discounted line amount."""
    query = select(TaxRate).where(
        TaxRate.is_active.is_(True),
        TaxRate.country_code == country_code,
    )

    if region_code:
        query = query.where(
            (TaxRate.region_code == region_code)
            | (TaxRate.region_code.is_(None))
        )
    else:
        query = query.where(TaxRate.region_code.is_(None))

    if product.category_id is not None:
        query = query.where(
            (TaxRate.category_id == product.category_id)
            | (TaxRate.category_id.is_(None))
        )
        query = query.order_by(TaxRate.category_id.is_(None), TaxRate.id.desc())
    else:
        query = query.where(TaxRate.category_id.is_(None))
        query = query.order_by(TaxRate.id.desc())

    tax = db.scalars(query.limit(1)).first()
    if tax is None:
        return amount, ZERO

    rate = Decimal(tax.rate_percent) / Decimal("100")

    if tax.is_inclusive:
        base = money(amount / (Decimal("1") + rate))
        return base, money(amount - base)

    return amount, money(amount * rate)


def _resolve_variant(
    db: Session, cart_item: CartItem, product: Product
) -> ProductVariant | None:
    active_variants = db.scalar(
        select(func.count(ProductVariant.id)).where(
            ProductVariant.product_id == product.id,
            ProductVariant.is_active.is_(True),
        )
    )

    if cart_item.variant_id is None:
        if active_variants:
            raise HTTPException(
                status_code=409,
                detail=f"Product {product.id} requires a variant to be selected",
            )
        return None

    variant = db.get(ProductVariant, cart_item.variant_id)
    if (
        variant is None
        or not variant.is_active
        or variant.product_id != product.id
    ):
        raise HTTPException(
            status_code=409, detail="A cart variant is unavailable"
        )
    return variant


def _load_stock(db, product, variant, lock):
    if variant is not None:
        query = select(VariantInventory).where(
            VariantInventory.variant_id == variant.id
        )
    else:
        query = select(Inventory).where(Inventory.product_id == product.id)
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def _load_coupon(db, code, lock, now):
    query = select(Coupon).where(Coupon.code == code.strip().upper())
    if lock:
        query = query.with_for_update()
    coupon = db.scalar(query)

    if coupon is None or not coupon.is_active:
        raise HTTPException(status_code=400, detail="Coupon is unavailable")
    if now < utc_value(coupon.starts_at) or now >= utc_value(coupon.expires_at):
        raise HTTPException(
            status_code=400, detail="Coupon is outside its validity period"
        )
    if coupon.usage_limit is not None and coupon.used_count >= coupon.usage_limit:
        raise HTTPException(status_code=400, detail="Coupon usage limit reached")
    return coupon


def _best_promotion(db, product, line_subtotal, quantity, now):
    query = select(Promotion).where(
        Promotion.is_active.is_(True),
        Promotion.starts_at <= now.replace(tzinfo=None),
        Promotion.expires_at > now.replace(tzinfo=None),
        (Promotion.usage_limit.is_(None))
        | (Promotion.used_count < Promotion.usage_limit),
        (Promotion.target_type == "ALL")
        | (
            (Promotion.target_type == "PRODUCT")
            & (Promotion.product_id == product.id)
        )
        | (
            (Promotion.target_type == "CATEGORY")
            & (Promotion.category_id == product.category_id)
        ),
    ).order_by(Promotion.priority.desc(), Promotion.id.desc())

    best, best_discount = None, ZERO
    for promotion in db.scalars(query).all():
        if promotion.discount_type == "PERCENTAGE":
            discount = line_subtotal * Decimal(promotion.discount_value) / 100
        else:
            discount = Decimal(promotion.discount_value) * quantity
        discount = min(line_subtotal, money(discount))
        if discount > best_discount:
            best, best_discount = promotion, discount
    return best, best_discount


def price_cart(
    db: Session,
    cart_items: list[CartItem],
    *,
    shipping_method_id: int | None = None,
    coupon_code: str | None = None,
    country: str = "IN",
    region: str | None = None,
    lock: bool = False,
    check_stock: bool = True,
) -> PricingResult:
    if not cart_items:
        raise HTTPException(status_code=409, detail="Cart is empty")

    now = datetime.now(timezone.utc)
    result = PricingResult()

    if shipping_method_id is not None:
        query = select(ShippingMethod).where(
            ShippingMethod.id == shipping_method_id,
            ShippingMethod.is_active.is_(True),
        )
        if lock:
            query = query.with_for_update()
        result.shipping_method = db.scalar(query)
        if result.shipping_method is None:
            raise HTTPException(
                status_code=400, detail="Shipping method is unavailable"
            )

    if coupon_code:
        result.coupon = _load_coupon(db, coupon_code, lock, now)

    for cart_item in cart_items:
        product = db.get(Product, cart_item.product_id)
        if product is None or not product.is_active:
            raise HTTPException(
                status_code=409, detail="A cart product is unavailable"
            )
        if cart_item.quantity <= 0:
            raise HTTPException(status_code=409, detail="Invalid cart quantity")

        variant = _resolve_variant(db, cart_item, product)
        stock = _load_stock(db, product, variant, lock)

        if check_stock and (
            stock is None or stock.available_quantity < cart_item.quantity
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for product {product.id}",
            )

        base_price = (
            variant.price
            if variant is not None and variant.price is not None
            else product.price
        )
        unit_price = money(Decimal(str(base_price)))
        line_subtotal = money(unit_price * cart_item.quantity)

        promotion, promo_discount = _best_promotion(
            db, product, line_subtotal, cart_item.quantity, now
        )
        result.lines.append(
            PricedLine(
                cart_item=cart_item,
                product=product,
                variant=variant,
                stock=stock,
                unit_price=unit_price,
                line_subtotal=line_subtotal,
                promotion=promotion,
                promotion_discount=promo_discount,
            )
        )
        result.subtotal += line_subtotal
        result.promotion_discount_total += promo_discount
        result.item_count += cart_item.quantity

    result.subtotal = money(result.subtotal)
    result.promotion_discount_total = money(result.promotion_discount_total)

    remaining = max(ZERO, result.subtotal - result.promotion_discount_total)

    if result.coupon is not None:
        if result.subtotal < Decimal(result.coupon.min_order_amount):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Coupon requires a minimum subtotal of "
                    f"{result.coupon.min_order_amount}"
                ),
            )
        if result.coupon.discount_type == "PERCENTAGE":
            coupon_discount = money(
                remaining * Decimal(result.coupon.discount_value) / 100
            )
        else:
            coupon_discount = money(result.coupon.discount_value)
        result.coupon_discount = min(remaining, coupon_discount)

    # Spread the coupon proportionally over lines; last line absorbs rounding.
    allocated = ZERO
    for index, line in enumerate(result.lines):
        line_remaining = line.line_subtotal - line.promotion_discount
        if result.coupon_discount and remaining > 0:
            if index == len(result.lines) - 1:
                share = result.coupon_discount - allocated
            else:
                share = money(
                    result.coupon_discount * line_remaining / remaining
                )
            share = max(ZERO, min(line_remaining, share))
        else:
            share = ZERO
        allocated += share

        line.coupon_share = share
        line.discount = money(line.promotion_discount + share)
        discounted = max(ZERO, line.line_subtotal - line.discount)
        line.tax_base, line.tax = calculate_tax(
            db, discounted, line.product, country.upper(), region
        )
        line.line_total = money(line.tax_base + line.tax)
        result.tax_total += line.tax

    result.discount_total = money(
        result.promotion_discount_total + result.coupon_discount
    )
    result.tax_total = money(result.tax_total)
    discounted_subtotal = max(ZERO, result.subtotal - result.discount_total)

    method = result.shipping_method
    if method is not None:
        free = (
            method.free_shipping_threshold is not None
            and discounted_subtotal >= method.free_shipping_threshold
        )
        result.shipping_total = ZERO if free else money(method.base_fee)

    # line_total already contains tax once (exclusive: base+tax, inclusive:
    # the discounted amount), so inclusive taxes are never double counted.
    result.grand_total = money(
        sum((line.line_total for line in result.lines), ZERO)
        + result.shipping_total
    )
    return result
