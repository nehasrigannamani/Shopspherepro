
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.db.session import get_db
from app.models.product import Product
from app.models.product_price_history import ProductPriceHistory
from app.models.user import User, UserRole
from app.schemas.product import ProductResponse
from app.schemas.product_pricing import (
    ProductPriceHistoryResponse,
    ProductPriceUpdate,
)


router = APIRouter(prefix="/products", tags=["Product Pricing"])

PRICING_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.PRODUCT_MANAGER,
)


def get_product_or_404(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)

    if product is None:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    return product


@router.patch("/{product_id}/price", response_model=ProductResponse)
def update_product_price(
    product_id: int,
    payload: ProductPriceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*PRICING_ROLES)),
):
    product = get_product_or_404(db, product_id)

    old_price = product.price
    old_compare_at_price = product.compare_at_price

    new_price = (
        payload.price if payload.price is not None else old_price
    )
    new_compare_at_price = (
        payload.compare_at_price
        if payload.compare_at_price is not None
        else old_compare_at_price
    )

    if (
        new_compare_at_price is not None
        and new_compare_at_price < new_price
    ):
        raise HTTPException(
            status_code=422,
            detail="compare_at_price must be greater than or equal to price",
        )

    if (
        new_price == old_price
        and new_compare_at_price == old_compare_at_price
    ):
        raise HTTPException(
            status_code=422,
            detail="The submitted prices are unchanged",
        )

    history = ProductPriceHistory(
        product_id=product.id,
        old_price=old_price,
        new_price=new_price,
        old_compare_at_price=old_compare_at_price,
        new_compare_at_price=new_compare_at_price,
        changed_by=current_user.id,
        reason=payload.reason,
        notes=payload.notes,
    )

    product.price = new_price
    product.compare_at_price = new_compare_at_price
    db.add(history)

    try:
        db.commit()
        db.refresh(product)
    except Exception:
        db.rollback()
        raise

    return product


@router.get(
    "/{product_id}/price-history",
    response_model=list[ProductPriceHistoryResponse],
)
def get_product_price_history(
    product_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*PRICING_ROLES)),
):
    get_product_or_404(db, product_id)

    statement = (
        select(ProductPriceHistory)
        .where(ProductPriceHistory.product_id == product_id)
        .order_by(ProductPriceHistory.created_at.desc(),
                  ProductPriceHistory.id.desc())
        .offset(offset)
        .limit(limit)
    )

    return list(db.scalars(statement).all())
