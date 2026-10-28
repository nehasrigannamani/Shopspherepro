from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.shipping import ShippingMethod
from app.models.user import User, UserRole
from app.schemas.shipping import (
    ShippingCalculateRequest,
    ShippingCalculateResponse,
    ShippingMethodCreate,
    ShippingMethodResponse,
    ShippingMethodUpdate,
)

router = APIRouter(prefix="/shipping", tags=["Shipping Configuration"])

MANAGER_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.ORDER_MANAGER,
}
CENT = Decimal("0.01")


def require_manager(user: User) -> None:
    if user.role not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized to manage shipping")


def get_method_or_404(db: Session, method_id: int) -> ShippingMethod:
    method = db.get(ShippingMethod, method_id)
    if method is None:
        raise HTTPException(status_code=404, detail="Shipping method not found")
    return method


def validate_delivery_days(min_days: int, max_days: int) -> None:
    if max_days < min_days:
        raise HTTPException(
            status_code=422,
            detail="estimated_max_days must be greater than or equal to estimated_min_days",
        )


@router.post("", response_model=ShippingMethodResponse, status_code=201)
def create_shipping_method(
    payload: ShippingMethodCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)

    code = payload.code.strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="Shipping code cannot be empty")

    if db.scalar(
        select(ShippingMethod.id).where(ShippingMethod.code == code)
    ) is not None:
        raise HTTPException(status_code=409, detail="Shipping code already exists")

    validate_delivery_days(
        payload.estimated_min_days,
        payload.estimated_max_days,
    )

    data = payload.model_dump()
    data["code"] = code
    method = ShippingMethod(**data)
    db.add(method)

    try:
        db.commit()
        db.refresh(method)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Shipping code already exists")

    return method


@router.get("/methods", response_model=list[ShippingMethodResponse])
def list_shipping_methods(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Customers see active methods only; managers can see all methods.
    query = select(ShippingMethod).order_by(ShippingMethod.id.desc())

    if current_user.role not in MANAGER_ROLES:
        query = query.where(ShippingMethod.is_active.is_(True))

    return list(db.scalars(query).all())


@router.get("/methods/{method_id}", response_model=ShippingMethodResponse)
def get_shipping_method(
    method_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    method = get_method_or_404(db, method_id)

    if not method.is_active and current_user.role not in MANAGER_ROLES:
        raise HTTPException(status_code=404, detail="Shipping method not found")

    return method


@router.patch("/methods/{method_id}", response_model=ShippingMethodResponse)
def update_shipping_method(
    method_id: int,
    payload: ShippingMethodUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    method = get_method_or_404(db, method_id)
    changes = payload.model_dump(exclude_unset=True)

    min_days = changes.get("estimated_min_days", method.estimated_min_days)
    max_days = changes.get("estimated_max_days", method.estimated_max_days)
    validate_delivery_days(min_days, max_days)

    for field, value in changes.items():
        setattr(method, field, value)

    db.commit()
    db.refresh(method)
    return method


@router.delete("/methods/{method_id}", status_code=204)
def deactivate_shipping_method(
    method_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    method = get_method_or_404(db, method_id)
    method.is_active = False
    db.commit()
    return None


@router.post("/calculate", response_model=ShippingCalculateResponse)
def calculate_shipping(
    payload: ShippingCalculateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    method = get_method_or_404(db, payload.method_id)

    if not method.is_active:
        raise HTTPException(status_code=400, detail="Shipping method is inactive")

    subtotal = payload.order_subtotal.quantize(CENT, rounding=ROUND_HALF_UP)

    is_free = (
        method.free_shipping_threshold is not None
        and subtotal >= method.free_shipping_threshold
    )

    fee = Decimal("0.00") if is_free else method.base_fee
    fee = fee.quantize(CENT, rounding=ROUND_HALF_UP)

    return ShippingCalculateResponse(
        method_id=method.id,
        method_name=method.name,
        order_subtotal=subtotal,
        shipping_fee=fee,
        is_free_shipping=is_free,
        estimated_min_days=method.estimated_min_days,
        estimated_max_days=method.estimated_max_days,
    )