
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.db.session import get_db
from app.models.category import Category
from app.models.product import Product
from app.models.user import User, UserRole
from app.schemas.category import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
)


router = APIRouter(prefix="/categories", tags=["Categories"])

MANAGER_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.PRODUCT_MANAGER,
)


@router.post(
    "",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_category(
    payload: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    if payload.parent_id is not None:
        parent = db.get(Category, payload.parent_id)
        if parent is None or not parent.is_active:
            raise HTTPException(404, "Active parent category not found")

    category = Category(**payload.model_dump())
    db.add(category)

    try:
        db.commit()
        db.refresh(category)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, "Category name or slug already exists"
        ) from None

    return category


@router.get("", response_model=list[CategoryResponse])
def list_categories(
    include_inactive: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    statement = select(Category)

    if not include_inactive:
        statement = statement.where(Category.is_active.is_(True))

    return list(
        db.scalars(
            statement.order_by(Category.name).offset(offset).limit(limit)
        ).all()
    )


@router.get("/{category_id}", response_model=CategoryResponse)
def get_category(category_id: int, db: Session = Depends(get_db)):
    category = db.get(Category, category_id)

    if category is None or not category.is_active:
        raise HTTPException(404, "Category not found")

    return category


@router.patch("/{category_id}", response_model=CategoryResponse)
def update_category(
    category_id: int,
    payload: CategoryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    category = db.get(Category, category_id)

    if category is None:
        raise HTTPException(404, "Category not found")

    changes = payload.model_dump(exclude_unset=True)

    if changes.get("parent_id") == category_id:
        raise HTTPException(400, "A category cannot be its own parent")

    if changes.get("parent_id") is not None:
        parent = db.get(Category, changes["parent_id"])
        if parent is None or not parent.is_active:
            raise HTTPException(404, "Active parent category not found")

        ancestor = parent
        while ancestor is not None:
            if ancestor.id == category_id:
                raise HTTPException(
                    400, "Category hierarchy cannot contain a cycle"
                )
            ancestor = (
                db.get(Category, ancestor.parent_id)
                if ancestor.parent_id is not None
                else None
            )

    for field, value in changes.items():
        setattr(category, field, value)

    try:
        db.commit()
        db.refresh(category)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, "Category name or slug already exists"
        ) from None

    return category


@router.delete("/{category_id}", response_model=CategoryResponse)
def deactivate_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    category = db.get(Category, category_id)

    if category is None:
        raise HTTPException(404, "Category not found")

    has_products = db.scalar(
        select(Product.id).where(Product.category_id == category_id).limit(1)
    )
    has_children = db.scalar(
        select(Category.id).where(Category.parent_id == category_id).limit(1)
    )

    if has_products or has_children:
        raise HTTPException(
            409, "Category has products or subcategories and cannot be deactivated"
        )

    category.is_active = False
    db.commit()
    db.refresh(category)
    return category
