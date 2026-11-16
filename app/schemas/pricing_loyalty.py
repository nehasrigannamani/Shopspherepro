from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _utc(dt: datetime | None):
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


# ---------------------------------------------------------------- price breakdown
class AppliedPromotion(BaseModel):
    promotion_id: int
    name: str
    discount: Decimal


class BreakdownLine(BaseModel):
    product_id: int
    variant_id: int | None
    name: str
    sku: str
    quantity: int
    unit_price: Decimal
    line_subtotal: Decimal
    promotions: list[AppliedPromotion]
    promotion_discount: Decimal
    coupon_share: Decimal
    loyalty_share: Decimal
    discount_total: Decimal
    tax: Decimal
    line_total: Decimal


class BreakdownCoupon(BaseModel):
    code: str
    discount: Decimal


class BreakdownLoyalty(BaseModel):
    points_redeemed: int
    discount: Decimal
    available_points: int
    max_redeemable_points: int
    min_redeem_points: int
    estimated_points_to_earn: int


class PriceBreakdown(BaseModel):
    currency: str
    lines: list[BreakdownLine]
    subtotal: Decimal
    promotions: list[AppliedPromotion]
    promotion_discount: Decimal
    coupon: BreakdownCoupon | None
    loyalty: BreakdownLoyalty
    discount_total: Decimal
    tax_total: Decimal
    shipping_total: Decimal
    grand_total: Decimal
    note: str


class CouponEligibility(BaseModel):
    code: str
    eligible: bool
    reasons: list[str]
    min_order_amount: Decimal | None = None
    max_discount: Decimal | None = None
    estimated_discount: Decimal | None = None


# ---------------------------------------------------------------- promotion / coupon rules
class PromotionRulesUpdate(BaseModel):
    """Only the fields sent are changed; an explicit null clears an optional rule."""

    priority: int | None = Field(default=None, ge=1, le=1000)
    min_cart_value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    max_discount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    stackable: bool | None = None
    allow_with_coupon: bool | None = None
    per_customer_limit: int | None = Field(default=None, ge=1, le=100000)
    customer_segment_id: int | None = Field(default=None, ge=1)


class CouponRulesUpdate(BaseModel):
    max_discount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    per_customer_limit: int | None = Field(default=None, ge=1, le=100000)
    customer_segment_id: int | None = Field(default=None, ge=1)


class PromotionV2Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    discount_type: str
    discount_value: Decimal
    target_type: str
    product_id: int | None
    category_id: int | None
    starts_at: datetime
    expires_at: datetime
    usage_limit: int | None
    used_count: int
    priority: int
    is_active: bool
    min_cart_value: Decimal | None
    max_discount: Decimal | None
    stackable: bool
    allow_with_coupon: bool
    per_customer_limit: int | None
    customer_segment_id: int | None


class PromotionPage(BaseModel):
    items: list[PromotionV2Out]
    total: int
    page: int
    page_size: int


class CouponV2Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    discount_type: str
    discount_value: Decimal
    min_order_amount: Decimal
    usage_limit: int | None
    used_count: int
    is_active: bool
    max_discount: Decimal | None
    per_customer_limit: int | None
    customer_segment_id: int | None


class RedemptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    user_id: int
    promotion_id: int | None
    coupon_id: int | None
    discount_amount: Decimal
    status: str
    reversed_at: datetime | None
    created_at: datetime


class RedemptionPage(BaseModel):
    items: list[RedemptionOut]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------- segments
class SegmentCreate(BaseModel):
    code: str = Field(min_length=2, max_length=50, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=500)


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    is_active: bool
    created_at: datetime


class SegmentPage(BaseModel):
    items: list[SegmentOut]
    total: int
    page: int
    page_size: int


class MemberAdd(BaseModel):
    user_id: int = Field(ge=1)


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    segment_id: int
    user_id: int
    created_at: datetime


class MemberPage(BaseModel):
    items: list[MemberOut]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------- loyalty
class LoyaltyAccount(BaseModel):
    user_id: int
    balance: int
    available_points: int
    expiring_within_30_days: int
    lifetime_earned: int
    point_value: Decimal
    min_redeem_points: int
    max_redeem_percent: int


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entry_type: str
    points: int
    points_remaining: int
    expires_at: datetime | None
    order_id: int | None
    reason: str | None
    created_at: datetime


class LedgerPage(BaseModel):
    items: list[LedgerEntryOut]
    total: int
    page: int
    page_size: int


class PointsAdjust(BaseModel):
    points: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def _nonzero(self):
        if self.points == 0:
            raise ValueError("points must not be zero")
        return self


class CreditResult(BaseModel):
    order_id: int
    points_credited: int
    already_credited: bool


class ExpireResult(BaseModel):
    expired_lots: int


class RuleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    earn_rate: Decimal = Field(gt=0, le=1000, max_digits=10, decimal_places=4)
    min_order_value: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    max_points_per_order: int | None = Field(default=None, ge=1, le=10_000_000)
    category_id: int | None = Field(default=None, ge=1)
    segment_id: int | None = Field(default=None, ge=1)
    priority: int = Field(default=1, ge=1, le=1000)
    expiry_days: int | None = Field(default=None, ge=0, le=3650)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _window(self):
        if self.starts_at and self.ends_at and _utc(self.ends_at) <= _utc(self.starts_at):
            raise ValueError("ends_at must be after starts_at")
        return self


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    earn_rate: Decimal | None = Field(default=None, gt=0, le=1000, max_digits=10, decimal_places=4)
    min_order_value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    max_points_per_order: int | None = Field(default=None, ge=1, le=10_000_000)
    category_id: int | None = Field(default=None, ge=1)
    segment_id: int | None = Field(default=None, ge=1)
    priority: int | None = Field(default=None, ge=1, le=1000)
    expiry_days: int | None = Field(default=None, ge=0, le=3650)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_active: bool | None = None


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    earn_rate: Decimal
    min_order_value: Decimal
    max_points_per_order: int | None
    category_id: int | None
    segment_id: int | None
    priority: int
    expiry_days: int | None
    starts_at: datetime | None
    ends_at: datetime | None
    is_active: bool


class RulePage(BaseModel):
    items: list[RuleOut]
    total: int
    page: int
    page_size: int
