from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.category import Category
from app.models.product import Product
from app.models.promotion import Promotion
from app.models.user import User, UserRole
from app.schemas.promotion import (
    PromotionCalculateRequest,
    PromotionCalculateResponse,
    PromotionCreate,
    PromotionResponse,
    PromotionUpdate,
)

router = APIRouter(prefix="/promotions", tags=["Promotion Management"])

MANAGER_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.PRODUCT_MANAGER,
}
CENT = Decimal("0.01")


def require_manager(user: User) -> None:
    if user.role not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized to manage promotions")


def utc_value(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_promotion_or_404(db: Session, promotion_id: int) -> Promotion:
    promotion = db.get(Promotion, promotion_id)
    if promotion is None:
        raise HTTPException(status_code=404, detail="Promotion not found")
    return promotion


def validate_target(db: Session, payload: PromotionCreate) -> None:
    if payload.target_type == "PRODUCT":
        product = db.get(Product, payload.product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Target product not found")
    elif payload.target_type == "CATEGORY":
        category = db.get(Category, payload.category_id)
        if category is None:
            raise HTTPException(status_code=404, detail="Target category not found")


@router.post("", response_model=PromotionResponse, status_code=201)
def create_promotion(
    payload: PromotionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    validate_target(db, payload)

    promotion = Promotion(**payload.model_dump())
    db.add(promotion)
    db.commit()
    db.refresh(promotion)
    return promotion


@router.get("", response_model=list[PromotionResponse])
def list_promotions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    return list(
        db.scalars(select(Promotion).order_by(Promotion.priority.desc(), Promotion.id.desc())).all()
    )


@router.get("/{promotion_id}", response_model=PromotionResponse)
def get_promotion(
    promotion_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    return get_promotion_or_404(db, promotion_id)


@router.patch("/{promotion_id}", response_model=PromotionResponse)
def update_promotion(
    promotion_id: int,
    payload: PromotionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    promotion = get_promotion_or_404(db, promotion_id)
    changes = payload.model_dump(exclude_unset=True)

    new_discount = changes.get("discount_value", promotion.discount_value)
    new_start = changes.get("starts_at", promotion.starts_at)
    new_expiry = changes.get("expires_at", promotion.expires_at)

    if promotion.discount_type == "PERCENTAGE" and new_discount is not None:
        if new_discount > 100:
            raise HTTPException(status_code=422, detail="Percentage discount cannot exceed 100")

    if new_start is not None and new_expiry is not None:
        if utc_value(new_expiry) <= utc_value(new_start):
            raise HTTPException(status_code=422, detail="expires_at must be later than starts_at")

    for field, value in changes.items():
        setattr(promotion, field, value)

    db.commit()
    db.refresh(promotion)
    return promotion


@router.delete("/{promotion_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_promotion(
    promotion_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    promotion = get_promotion_or_404(db, promotion_id)
    promotion.is_active = False
    db.commit()
    return None


@router.post("/calculate", response_model=PromotionCalculateResponse)
def calculate_promotion(
    payload: PromotionCalculateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != UserRole.CUSTOMER:
        raise HTTPException(status_code=403, detail="Only customers can calculate promotions")

    product = db.get(Product, payload.product_id)
    if product is None or not product.is_active:
        raise HTTPException(status_code=404, detail="Active product not found")

    # Use the current database price, not a client-supplied price.
    unit_price = Decimal(str(product.price)).quantize(CENT, rounding=ROUND_HALF_UP)
    original_total = (unit_price * payload.quantity).quantize(CENT, rounding=ROUND_HALF_UP)
    now = datetime.now(timezone.utc)

    category_id = product.category_id
    conditions = [
        Promotion.is_active.is_(True),
        Promotion.starts_at <= now.replace(tzinfo=None),
        Promotion.expires_at > now.replace(tzinfo=None),
        (Promotion.usage_limit.is_(None)) | (Promotion.used_count < Promotion.usage_limit),
        (
            (Promotion.target_type == "ALL")
            | (
                (Promotion.target_type == "PRODUCT")
                & (Promotion.product_id == product.id)
            )
            | (
                (Promotion.target_type == "CATEGORY")
                & (Promotion.category_id == category_id)
            )
        ),
    ]

    promotions = list(
        db.scalars(
            select(Promotion)
            .where(*conditions)
            .order_by(Promotion.priority.desc(), Promotion.id.desc())
        ).all()
    )

    best_promotion = None
    best_discount = Decimal("0.00")

    for promotion in promotions:
        if promotion.discount_type == "PERCENTAGE":
            amount = original_total * promotion.discount_value / Decimal("100")
        else:
            amount = promotion.discount_value * payload.quantity

        amount = min(
            original_total,
            amount.quantize(CENT, rounding=ROUND_HALF_UP),
        )

        if amount > best_discount:
            best_discount = amount
            best_promotion = promotion

    return PromotionCalculateResponse(
        product_id=product.id,
        original_total=original_total,
        discount_amount=best_discount,
        discounted_total=(original_total - best_discount).quantize(CENT),
        promotion_id=best_promotion.id if best_promotion else None,
        promotion_name=best_promotion.name if best_promotion else None,
    )