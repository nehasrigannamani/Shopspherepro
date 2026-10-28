
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.db.session import get_db
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.user import User, UserRole
from app.schemas.product_variant import (
    ProductVariantCreate,
    ProductVariantResponse,
    ProductVariantUpdate,
)


router = APIRouter(
    prefix="/products/{product_id}/variants",
    tags=["Product Variants"],
)

MANAGER_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.PRODUCT_MANAGER,
)


def get_active_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)

    if product is None or not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active product not found",
        )

    return product


def get_variant(
    db: Session,
    product_id: int,
    variant_id: int,
) -> ProductVariant:
    variant = db.scalar(
        select(ProductVariant).where(
            ProductVariant.id == variant_id,
            ProductVariant.product_id == product_id,
        )
    )

    if variant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product variant not found",
        )

    return variant


@router.post(
    "",
    response_model=ProductVariantResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_variant(
    product_id: int,
    payload: ProductVariantCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    if payload.product_id != product_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="product_id in the request body must match the URL",
        )

    product = get_active_product(db, product_id)

    variant = ProductVariant(
        product_id=product.id,
        sku=payload.sku,
        option_values=payload.option_values,
        price=payload.price,
        is_active=True,
    )
    db.add(variant)

    try:
        db.commit()
        db.refresh(variant)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A variant with this SKU already exists",
        ) from None

    return variant


@router.get("", response_model=list[ProductVariantResponse])
def list_variants(
    product_id: int,
    include_inactive: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    get_active_product(db, product_id)

    statement = select(ProductVariant).where(
        ProductVariant.product_id == product_id
    )

    if not include_inactive:
        statement = statement.where(ProductVariant.is_active.is_(True))

    statement = (
        statement.order_by(ProductVariant.id)
        .offset(offset)
        .limit(limit)
    )

    return list(db.scalars(statement).all())


@router.get("/{variant_id}", response_model=ProductVariantResponse)
def retrieve_variant(
    product_id: int,
    variant_id: int,
    db: Session = Depends(get_db),
):
    get_active_product(db, product_id)
    variant = get_variant(db, product_id, variant_id)

    if not variant.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active product variant not found",
        )

    return variant


@router.patch(
    "/{variant_id}",
    response_model=ProductVariantResponse,
)
def update_variant(
    product_id: int,
    variant_id: int,
    payload: ProductVariantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    get_active_product(db, product_id)
    variant = get_variant(db, product_id, variant_id)

    changes = payload.model_dump(exclude_unset=True)

    for field, value in changes.items():
        setattr(variant, field, value)

    try:
        db.commit()
        db.refresh(variant)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Variant update conflicts with an existing SKU or database constraint",
        ) from None

    return variant


@router.delete(
    "/{variant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def deactivate_variant(
    product_id: int,
    variant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    get_active_product(db, product_id)
    variant = get_variant(db, product_id, variant_id)

    if not variant.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Variant is already inactive",
        )

    variant.is_active = False
    db.commit()

    return None
