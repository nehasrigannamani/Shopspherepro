
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_roles
from app.db.session import get_db
from app.models.brand import Brand
from app.models.category import Category
from app.models.inventory import Inventory
from app.models.product import Product
from app.models.user import User, UserRole
from app.schemas.product import (
    ProductCreate,
    ProductResponse,
    ProductUpdate,
)


router = APIRouter(prefix="/products", tags=["Products"])

PRODUCT_MANAGER_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.PRODUCT_MANAGER,
)


def validate_category_and_brand(
    db: Session,
    category_id: int | None,
    brand_id: int | None,
) -> Category | None:
    """Validate the optional category and brand references."""

    category = None

    if category_id is not None:
        category = db.get(Category, category_id)

        if category is None or not category.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Active category not found",
            )

    if brand_id is not None:
        brand = db.get(Brand, brand_id)

        if brand is None or not brand.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Active brand not found",
            )

    return category


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_roles(*PRODUCT_MANAGER_ROLES)
    ),
):
    """Create a product. Only authorized catalog managers may do this."""

    data = payload.model_dump()

    category = validate_category_and_brand(
        db=db,
        category_id=payload.category_id,
        brand_id=payload.brand_id,
    )

    if category is not None:
        data["category"] = category.name
    elif not payload.category:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide category_id or category",
        )

    if (
        payload.compare_at_price is not None
        and payload.compare_at_price < payload.price
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="compare_at_price must be greater than or equal to price",
        )

    data["sku"] = payload.sku.strip().upper()
    data["name"] = payload.name.strip()

    product = Product(**data, is_active=True)
    db.add(product)

    try:
        db.commit()
        db.refresh(product)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A product with this SKU already exists or the data violates a database constraint",
        ) from None

    return product


@router.get("", response_model=list[ProductResponse])
def list_products(
    q: str | None = Query(default=None, max_length=200),
    category_id: int | None = Query(default=None, gt=0),
    brand_id: int | None = Query(default=None, gt=0),
    category: str | None = Query(default=None, max_length=100),
    min_price: Decimal | None = Query(default=None, ge=0),
    max_price: Decimal | None = Query(default=None, ge=0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List active products with optional search and filters."""

    if (
        min_price is not None
        and max_price is not None
        and min_price > max_price
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_price cannot exceed max_price",
        )

    statement = select(Product).where(Product.is_active.is_(True))

    if q and q.strip():
        search_term = f"%{q.strip()}%"
        statement = statement.where(
            or_(
                Product.name.ilike(search_term),
                Product.sku.ilike(search_term),
                Product.description.ilike(search_term),
            )
        )

    if category_id is not None:
        statement = statement.where(
            Product.category_id == category_id
        )

    if brand_id is not None:
        statement = statement.where(Product.brand_id == brand_id)

    if category and category.strip():
        statement = statement.where(
            Product.category.ilike(f"%{category.strip()}%")
        )

    if min_price is not None:
        statement = statement.where(Product.price >= min_price)

    if max_price is not None:
        statement = statement.where(Product.price <= max_price)

    statement = (
        statement.order_by(Product.id.desc())
        .offset(offset)
        .limit(limit)
    )

    return list(db.scalars(statement).all())


@router.get("/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
):
    """Retrieve one active product by its ID."""

    product = db.get(Product, product_id)

    if product is None or not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    return product


@router.patch("/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_roles(*PRODUCT_MANAGER_ROLES)
    ),
):
    """Update product fields without replacing unspecified values."""

    product = db.get(Product, product_id)

    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    changes = payload.model_dump(exclude_unset=True)

    # Validate and synchronize category references.
    if "category_id" in changes:
        category_id = changes["category_id"]

        if category_id is not None:
            category = validate_category_and_brand(
                db=db,
                category_id=category_id,
                brand_id=None,
            )
            changes["category"] = category.name
        elif not changes.get("category") and not product.category:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A category name is required when category_id is cleared",
            )

    # Validate a newly supplied brand reference.
    if "brand_id" in changes and changes["brand_id"] is not None:
        validate_category_and_brand(
            db=db,
            category_id=None,
            brand_id=changes["brand_id"],
        )

    # If only the legacy category name is supplied, keep it as text.
    if "category" in changes and changes["category"] is not None:
        changes["category"] = changes["category"].strip()

    if "sku" in changes and changes["sku"] is not None:
        changes["sku"] = changes["sku"].strip().upper()

    if "name" in changes and changes["name"] is not None:
        changes["name"] = changes["name"].strip()

    new_price = changes.get("price", product.price)
    new_compare_at_price = changes.get(
        "compare_at_price",
        product.compare_at_price,
    )

    if (
        new_compare_at_price is not None
        and new_compare_at_price < new_price
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="compare_at_price must be greater than or equal to price",
        )

    for field, value in changes.items():
        setattr(product, field, value)

    try:
        db.commit()
        db.refresh(product)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The update conflicts with existing data or a database constraint",
        ) from None

    return product


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_roles(*PRODUCT_MANAGER_ROLES)
    ),
):
    """Soft-deactivate a product instead of permanently deleting it."""

    product = db.get(Product, product_id)

    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    if not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Product is already inactive",
        )

    # Preserve the product record and its inventory/order references.
    product.is_active = False

    # Do not delete inventory or stock movement history.
    db.commit()

    return None
