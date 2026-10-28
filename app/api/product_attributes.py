
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.db.session import get_db
from app.models.category import Category
from app.models.product import Product
from app.models.product_attribute import (
    ProductAttribute,
    ProductAttributeValue,
)
from app.models.user import User, UserRole
from app.schemas.product_attribute import (
    ProductAttributeCreate,
    ProductAttributeResponse,
    ProductAttributeUpdate,
    ProductAttributeValueResponse,
    ProductAttributeValueSet,
)


router = APIRouter(prefix="/product-attributes", tags=["Product Attributes"])

MANAGER_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.PRODUCT_MANAGER,
)


def validate_attribute_value(data_type: str, value: str) -> str:
    normalized_type = data_type.lower()

    if normalized_type == "text":
        return value

    if normalized_type == "integer":
        try:
            parsed = int(value)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Attribute value must be an integer",
            ) from None
        return str(parsed)

    if normalized_type == "decimal":
        try:
            parsed = Decimal(value)
            if not parsed.is_finite():
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            raise HTTPException(
                status_code=422,
                detail="Attribute value must be a valid decimal number",
            ) from None
        return format(parsed, "f")

    if normalized_type == "boolean":
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return "true"
        if normalized in {"false", "0", "no"}:
            return "false"

        raise HTTPException(
            status_code=422,
            detail="Boolean values must be true or false",
        )

    raise HTTPException(
        status_code=422,
        detail="Unsupported attribute data type",
    )


def get_active_attribute(
    db: Session,
    attribute_id: int,
) -> ProductAttribute:
    attribute = db.get(ProductAttribute, attribute_id)

    if attribute is None or not attribute.is_active:
        raise HTTPException(
            status_code=404,
            detail="Active product attribute not found",
        )

    return attribute


def get_active_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)

    if product is None or not product.is_active:
        raise HTTPException(
            status_code=404,
            detail="Active product not found",
        )

    return product


def validate_category(
    db: Session,
    category_id: int | None,
) -> None:
    if category_id is None:
        return

    category = db.get(Category, category_id)
    if category is None or not category.is_active:
        raise HTTPException(
            status_code=422,
            detail="The selected category does not exist or is inactive",
        )


@router.post(
    "",
    response_model=ProductAttributeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_attribute(
    payload: ProductAttributeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    validate_category(db, payload.category_id)

    attribute = ProductAttribute(**payload.model_dump())
    db.add(attribute)

    try:
        db.commit()
        db.refresh(attribute)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Unable to create the attribute because of a database constraint",
        ) from None

    return attribute


@router.get("", response_model=list[ProductAttributeResponse])
def list_attributes(
    category_id: int | None = Query(default=None, gt=0),
    include_inactive: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    statement = select(ProductAttribute)

    if not include_inactive:
        statement = statement.where(ProductAttribute.is_active.is_(True))

    if category_id is not None:
        statement = statement.where(
            (ProductAttribute.category_id == category_id)
            | (ProductAttribute.category_id.is_(None))
        )

    statement = (
        statement.order_by(ProductAttribute.name)
        .offset(offset)
        .limit(limit)
    )

    return list(db.scalars(statement).all())


@router.get("/{attribute_id}", response_model=ProductAttributeResponse)
def retrieve_attribute(
    attribute_id: int,
    db: Session = Depends(get_db),
):
    return get_active_attribute(db, attribute_id)


@router.patch(
    "/{attribute_id}",
    response_model=ProductAttributeResponse,
)
def update_attribute(
    attribute_id: int,
    payload: ProductAttributeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    attribute = get_active_attribute(db, attribute_id)
    changes = payload.model_dump(exclude_unset=True)

    if "category_id" in changes:
        validate_category(db, changes["category_id"])

    new_data_type = changes.get("data_type")
    if new_data_type and new_data_type != attribute.data_type:
        existing_values = db.scalar(
            select(ProductAttributeValue.id)
            .where(ProductAttributeValue.attribute_id == attribute_id)
            .limit(1)
        )
        if existing_values is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot change data_type while product values exist. "
                    "Remove or migrate those values first."
                ),
            )

    for field, value in changes.items():
        setattr(attribute, field, value)

    try:
        db.commit()
        db.refresh(attribute)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Attribute update conflicts with a database constraint",
        ) from None

    return attribute


@router.delete(
    "/{attribute_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def deactivate_attribute(
    attribute_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    attribute = get_active_attribute(db, attribute_id)
    attribute.is_active = False
    db.commit()
    return None


@router.put(
    "/products/{product_id}/{attribute_id}",
    response_model=ProductAttributeValueResponse,
)
def set_product_attribute_value(
    product_id: int,
    attribute_id: int,
    payload: ProductAttributeValueSet,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    product = get_active_product(db, product_id)
    attribute = get_active_attribute(db, attribute_id)

    if (
        attribute.category_id is not None
        and attribute.category_id != product.category_id
    ):
        raise HTTPException(
            status_code=422,
            detail="This attribute is not applicable to the product category",
        )

    normalized_value = validate_attribute_value(
        attribute.data_type,
        payload.value,
    )

    value_record = db.scalar(
        select(ProductAttributeValue).where(
            ProductAttributeValue.product_id == product_id,
            ProductAttributeValue.attribute_id == attribute_id,
        )
    )

    if value_record is None:
        value_record = ProductAttributeValue(
            product_id=product_id,
            attribute_id=attribute_id,
            value=normalized_value,
        )
        db.add(value_record)
    else:
        value_record.value = normalized_value

    try:
        db.commit()
        db.refresh(value_record)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Could not save the product attribute value",
        ) from None

    return value_record


@router.get(
    "/products/{product_id}",
    response_model=list[ProductAttributeValueResponse],
)
def list_product_attribute_values(
    product_id: int,
    db: Session = Depends(get_db),
):
    get_active_product(db, product_id)

    statement = (
        select(ProductAttributeValue)
        .join(
            ProductAttribute,
            ProductAttribute.id == ProductAttributeValue.attribute_id,
        )
        .where(
            ProductAttributeValue.product_id == product_id,
            ProductAttribute.is_active.is_(True),
        )
        .order_by(ProductAttribute.name)
    )

    return list(db.scalars(statement).all())


@router.delete(
    "/products/{product_id}/{attribute_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_product_attribute_value(
    product_id: int,
    attribute_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    get_active_product(db, product_id)

    value_record = db.scalar(
        select(ProductAttributeValue).where(
            ProductAttributeValue.product_id == product_id,
            ProductAttributeValue.attribute_id == attribute_id,
        )
    )

    if value_record is None:
        raise HTTPException(
            status_code=404,
            detail="Product attribute value not found",
        )

    db.delete(value_record)
    db.commit()
    return None


@router.get("/filter/products")
def filter_products_by_attribute(
    attribute_id: int = Query(gt=0),
    value: str = Query(min_length=1, max_length=2000),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    attribute = get_active_attribute(db, attribute_id)

    if not attribute.is_filterable:
        raise HTTPException(
            status_code=422,
            detail="This attribute is not configured for filtering",
        )

    normalized_value = validate_attribute_value(attribute.data_type, value)

    statement = (
        select(Product)
        .join(
            ProductAttributeValue,
            ProductAttributeValue.product_id == Product.id,
        )
        .where(
            ProductAttributeValue.attribute_id == attribute_id,
            ProductAttributeValue.value == normalized_value,
            Product.is_active.is_(True),
        )
    )

    if attribute.category_id is not None:
        statement = statement.where(
            Product.category_id == attribute.category_id
        )

    statement = (
        statement.order_by(Product.name)
        .offset(offset)
        .limit(limit)
    )

    products = db.scalars(statement).all()

    return [
        {
            "id": product.id,
            "sku": product.sku,
            "name": product.name,
            "category": product.category,
            "category_id": product.category_id,
            "brand_id": product.brand_id,
            "price": product.price,
            "is_active": product.is_active,
        }
        for product in products
    ]
