from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.category import Category
from app.models.tax import TaxRate
from app.models.user import User, UserRole
from app.schemas.tax import (
    TaxCalculateRequest,
    TaxCalculateResponse,
    TaxRateCreate,
    TaxRateResponse,
    TaxRateUpdate,
)

router = APIRouter(prefix="/taxes", tags=["Tax Management"])

MANAGER_ROLES = {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.FINANCE_MANAGER}
CENT = Decimal("0.01")


def require_manager(user: User) -> None:
    if user.role not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized to manage tax rates")


def get_tax_or_404(db: Session, tax_id: int) -> TaxRate:
    tax = db.get(TaxRate, tax_id)
    if tax is None:
        raise HTTPException(status_code=404, detail="Tax rate not found")
    return tax


@router.post("", response_model=TaxRateResponse, status_code=201)
def create_tax_rate(
    payload: TaxRateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)

    code = payload.code.strip().upper()
    if db.scalar(select(TaxRate.id).where(TaxRate.code == code)) is not None:
        raise HTTPException(status_code=409, detail="Tax code already exists")

    if payload.category_id is not None and db.get(Category, payload.category_id) is None:
        raise HTTPException(status_code=404, detail="Category not found")

    data = payload.model_dump()
    data["code"] = code
    data["country_code"] = payload.country_code.upper()
    tax = TaxRate(**data)
    db.add(tax)

    try:
        db.commit()
        db.refresh(tax)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tax code already exists")

    return tax


@router.get("", response_model=list[TaxRateResponse])
def list_tax_rates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    return list(db.scalars(select(TaxRate).order_by(TaxRate.id.desc())).all())


@router.get("/{tax_id}", response_model=TaxRateResponse)
def get_tax_rate(
    tax_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    return get_tax_or_404(db, tax_id)


@router.patch("/{tax_id}", response_model=TaxRateResponse)
def update_tax_rate(
    tax_id: int,
    payload: TaxRateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    tax = get_tax_or_404(db, tax_id)
    changes = payload.model_dump(exclude_unset=True)

    category_id = changes.get("category_id", tax.category_id)
    if category_id is not None and db.get(Category, category_id) is None:
        raise HTTPException(status_code=404, detail="Category not found")

    if changes.get("country_code") is not None:
        changes["country_code"] = changes["country_code"].upper()

    for field, value in changes.items():
        setattr(tax, field, value)

    db.commit()
    db.refresh(tax)
    return tax


@router.delete("/{tax_id}", status_code=204)
def deactivate_tax_rate(
    tax_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_manager(current_user)
    tax = get_tax_or_404(db, tax_id)
    tax.is_active = False
    db.commit()
    return None


@router.post("/calculate", response_model=TaxCalculateResponse)
def calculate_tax(
    payload: TaxCalculateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in {
        UserRole.CUSTOMER,
        UserRole.SUPER_ADMIN,
        UserRole.ADMIN,
        UserRole.FINANCE_MANAGER,
        UserRole.ORDER_MANAGER,
    }:
        raise HTTPException(status_code=403, detail="Not authorized to calculate tax")

    country = payload.country_code.upper()

    query = select(TaxRate).where(
        TaxRate.is_active.is_(True),
        TaxRate.country_code == country,
    )

    if payload.region_code:
        query = query.where(
            (TaxRate.region_code == payload.region_code)
            | (TaxRate.region_code.is_(None))
        )
    else:
        query = query.where(TaxRate.region_code.is_(None))

    # Prefer an exact category rate over the general rate.
    if payload.category_id is not None:
        query = query.where(
            (TaxRate.category_id == payload.category_id)
            | (TaxRate.category_id.is_(None))
        )
        query = query.order_by(TaxRate.category_id.is_(None), TaxRate.id.desc())
    else:
        query = query.where(TaxRate.category_id.is_(None))
        query = query.order_by(TaxRate.id.desc())

    tax = db.scalars(query.limit(1)).first()

    if tax is None:
        return TaxCalculateResponse(
            base_amount=payload.amount.quantize(CENT),
            tax_amount=Decimal("0.00"),
            total_amount=payload.amount.quantize(CENT),
            rate_percent=Decimal("0.0000"),
            tax_name=None,
            tax_inclusive=False,
        )

    amount = payload.amount.quantize(CENT, rounding=ROUND_HALF_UP)
    rate = tax.rate_percent / Decimal("100")

    if tax.is_inclusive:
        # Amount already includes tax; extract the included tax portion.
        base = (amount / (Decimal("1") + rate)).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        tax_amount = (amount - base).quantize(CENT)
        total = amount
    else:
        base = amount
        tax_amount = (base * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        total = base + tax_amount

    return TaxCalculateResponse(
        base_amount=base,
        tax_amount=tax_amount,
        total_amount=total,
        rate_percent=tax.rate_percent,
        tax_name=tax.name,
        tax_inclusive=tax.is_inclusive,
    )