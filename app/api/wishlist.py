from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.product import Product
from app.models.user import User, UserRole
from app.models.wishlist import WishlistItem
from app.schemas.wishlist import (
    WishlistItemCreate,
    WishlistItemResponse,
    WishlistProductResponse,
)

router = APIRouter(prefix="/wishlist", tags=["Wishlist Management"])


def require_customer(user: User) -> None:
    if user.role != UserRole.CUSTOMER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only customers can manage a wishlist",
        )


@router.post(
    "/items",
    response_model=WishlistItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_to_wishlist(
    payload: WishlistItemCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_customer(current_user)

    product = db.get(Product, payload.product_id)
    if product is None or not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active product not found",
        )

    existing = db.scalar(
        select(WishlistItem).where(
            WishlistItem.user_id == current_user.id,
            WishlistItem.product_id == payload.product_id,
        )
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Product is already in your wishlist",
        )

    item = WishlistItem(
        user_id=current_user.id,
        product_id=payload.product_id,
    )
    db.add(item)

    try:
        db.commit()
        db.refresh(item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Product is already in your wishlist",
        )

    return item


@router.get("/items", response_model=list[WishlistProductResponse])
def get_wishlist(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_customer(current_user)

    rows = db.execute(
        select(WishlistItem, Product)
        .join(Product, Product.id == WishlistItem.product_id)
        .where(WishlistItem.user_id == current_user.id)
        .order_by(WishlistItem.created_at.desc())
    ).all()

    return [
        WishlistProductResponse(
            wishlist_item_id=item.id,
            product_id=product.id,
            name=product.name,
            sku=product.sku,
            price=product.price,
            image_url=product.image_url,
            created_at=item.created_at,
        )
        for item, product in rows
    ]


@router.delete("/items/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_wishlist(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_customer(current_user)

    item = db.scalar(
        select(WishlistItem).where(
            WishlistItem.user_id == current_user.id,
            WishlistItem.product_id == product_id,
        )
    )

    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found in your wishlist",
        )

    db.delete(item)
    db.commit()
    return None