from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.coupon import Coupon
from app.models.user import User, UserRole
from app.schemas.coupon import (
    CouponCreate,
    CouponResponse,
    CouponUpdate,
    CouponValidateRequest,
    CouponValidateResponse,
)

router = APIRouter(prefix="/coupons", tags=["Coupon Management"])

MANAGER_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.PRODUCT_MANAGER,
}
CENT = Decimal("0.01")


def require_manager(user: User) -> None:
    if user.role not in MANAGER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to manage coupons",
        )


def normalize_code(code: str) -> str:
    return code.strip().upper()


def as_utc(value: datetime) -> datetime:
    # MySQL may return DATETIME values without timezone information.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_coupon_or_404(db: Session, coupon_id: int) -> Coupon:
    coupon = db.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return coupon


def calculate_discount(coupon: Coupon, subtotal: Decimal) -> Decimal:
    if coupon.discount_type == "PERCENTAGE":
        discount = subtotal * coupon.discount_value / Decimal("100")
    else:
        discount = coupon.discount_value

    return min(
        subtotal,
        discount.quantize(CENT, rounding=ROUND_HALF_UP),
    )


def validate_coupon_rules(coupon: Coupon, subtotal: Decimal) -> None:
    now = datetime.now(timezone.utc)

    if not coupon.is_active:
        raise HTTPException(status_code=400, detail="Coupon is inactive")

    if now < as_utc(coupon.starts_at):
        raise HTTPException(status_code=400, detail="Coupon is not active yet")

    if now >= as_utc(coupon.expires_at):
        raise HTTPException(status_code=400, detail="Coupon has expired")

    if coupon.usage_limit is not None and coupon.used_count >= coupon.usage_limit:
        raise HTTPException(status_code=400, detail="Coupon usage limit reached")

    if subtotal < coupon.min_order_amount:
        raise HTTPException(
            status_code=400,
            detail=f"Minimum order subtotal is {coupon.min_order_amount}",
        )


@router.post(
    "",
    response_model=CouponResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_coupon(
    payload: CouponCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)

    code = normalize_code(payload.code)
    if db.scalar(select(Coupon.id).where(Coupon.code == code)) is not None:
        raise HTTPException(status_code=409, detail="Coupon code already exists")

    coupon = Coupon(**payload.model_dump(exclude={"code"}), code=code)
    db.add(coupon)

    try:
        db.commit()
        db.refresh(coupon)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Coupon code already exists")

    return coupon


@router.get("", response_model=list[CouponResponse])
def list_coupons(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    return list(db.scalars(select(Coupon).order_by(Coupon.id.desc())).all())


@router.get("/{coupon_id}", response_model=CouponResponse)
def get_coupon(
    coupon_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    return get_coupon_or_404(db, coupon_id)


@router.patch("/{coupon_id}", response_model=CouponResponse)
def update_coupon(
    coupon_id: int,
    payload: CouponUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    coupon = get_coupon_or_404(db, coupon_id)
    updates = payload.model_dump(exclude_unset=True)

    new_discount_type = coupon.discount_type
    new_discount_value = updates.get("discount_value", coupon.discount_value)
    new_starts_at = updates.get("starts_at", coupon.starts_at)
    new_expires_at = updates.get("expires_at", coupon.expires_at)

    if new_discount_type == "PERCENTAGE" and new_discount_value is not None:
        if new_discount_value > 100:
            raise HTTPException(
                status_code=422,
                detail="Percentage discount cannot exceed 100",
            )

    if new_starts_at is not None and new_expires_at is not None:
        if as_utc(new_expires_at) <= as_utc(new_starts_at):
            raise HTTPException(
                status_code=422,
                detail="expires_at must be later than starts_at",
            )

    for field, value in updates.items():
        setattr(coupon, field, value)

    try:
        db.commit()
        db.refresh(coupon)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Coupon update conflicts with stored data")

    return coupon


@router.delete("/{coupon_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_coupon(
    coupon_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    coupon = get_coupon_or_404(db, coupon_id)
    coupon.is_active = False
    db.commit()
    return None


@router.post("/validate", response_model=CouponValidateResponse)
def validate_coupon(
    payload: CouponValidateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != UserRole.CUSTOMER:
        raise HTTPException(
            status_code=403,
            detail="Only customers can validate coupons",
        )

    code = normalize_code(payload.code)
    coupon = db.scalar(select(Coupon).where(Coupon.code == code))

    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found")

    subtotal = payload.order_subtotal.quantize(CENT, rounding=ROUND_HALF_UP)
    validate_coupon_rules(coupon, subtotal)

    discount = calculate_discount(coupon, subtotal)

    return CouponValidateResponse(
        code=coupon.code,
        discount_type=coupon.discount_type,
        discount_value=coupon.discount_value,
        order_subtotal=subtotal,
        discount_amount=discount,
        payable_subtotal=(subtotal - discount).quantize(CENT),
    )