
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.db.session import get_db
from app.models.brand import Brand
from app.models.product import Product
from app.models.user import User, UserRole
from app.schemas.brand import BrandCreate, BrandResponse, BrandUpdate
from app.schemas.product import ProductResponse


router = APIRouter(prefix="/brands", tags=["Brands"])

MANAGER_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.PRODUCT_MANAGER,
)


@router.post(
    "",
    response_model=BrandResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_brand(
    payload: BrandCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    data = payload.model_dump()
    if data.get("website_url") is not None:
        data["website_url"] = str(data["website_url"])

    brand = Brand(**data, name=payload.name.strip())
    db.add(brand)

    try:
        db.commit()
        db.refresh(brand)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Brand name already exists") from None

    return brand


@router.get("", response_model=list[BrandResponse])
def list_brands(
    q: str | None = Query(default=None, max_length=120),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    statement = select(Brand).where(Brand.is_active.is_(True))

    if q and q.strip():
        statement = statement.where(Brand.name.ilike(f"%{q.strip()}%"))

    return list(
        db.scalars(statement.order_by(Brand.name).offset(offset).limit(limit)).all()
    )


@router.get("/{brand_id}/products", response_model=list[ProductResponse])
def list_brand_products(
    brand_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    brand = db.get(Brand, brand_id)
    if brand is None or not brand.is_active:
        raise HTTPException(404, "Brand not found")

    statement = (
        select(Product)
        .where(
            Product.brand_id == brand_id,
            Product.is_active.is_(True),
        )
        .order_by(Product.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(statement).all())


@router.get("/{brand_id}", response_model=BrandResponse)
def get_brand(brand_id: int, db: Session = Depends(get_db)):
    brand = db.get(Brand, brand_id)
    if brand is None or not brand.is_active:
        raise HTTPException(404, "Brand not found")
    return brand


@router.patch("/{brand_id}", response_model=BrandResponse)
def update_brand(
    brand_id: int,
    payload: BrandUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(404, "Brand not found")

    changes = payload.model_dump(exclude_unset=True)
    if changes.get("website_url") is not None:
        changes["website_url"] = str(changes["website_url"])

    for field, value in changes.items():
        setattr(brand, field, value)

    try:
        db.commit()
        db.refresh(brand)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Brand name already exists") from None

    return brand


@router.delete("/{brand_id}", response_model=BrandResponse)
def deactivate_brand(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(404, "Brand not found")

    has_products = db.scalar(
        select(Product.id).where(Product.brand_id == brand_id).limit(1)
    )
    if has_products:
        raise HTTPException(
            409, "Brand has products and cannot be deactivated"
        )

    brand.is_active = False
    db.commit()
    db.refresh(brand)
    return brand
