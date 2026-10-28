
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_roles
from app.db.session import get_db
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.product_review import ProductReview
from app.models.shipping_order import ShippingOrder
from app.models.user import User
from app.schemas.product_reviews import (
    ProductReviewCreate,
    ProductReviewResponse,
    ReviewModerationUpdate,
)


router = APIRouter(
    prefix="/reviews",
    tags=["Product Reviews and Ratings"],
)


STAFF_ROLES = (
    "SUPER_ADMIN",
    "ADMIN",
    "PRODUCT_MANAGER",
    "SUPPORT_AGENT",
)


def _is_order_delivered(db: Session, order: Order) -> bool:
    if order.status == "DELIVERED":
        return True

    shipping_order = (
        db.query(ShippingOrder)
        .filter(ShippingOrder.order_id == order.id)
        .first()
    )

    return shipping_order is not None and shipping_order.status == "DELIVERED"


@router.post(
    "",
    response_model=ProductReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_product_review(
    payload: ProductReviewCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    product = (
        db.query(Product)
        .filter(Product.id == payload.product_id)
        .first()
    )

    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    order_item = (
        db.query(OrderItem)
        .filter(OrderItem.id == payload.order_item_id)
        .first()
    )

    if order_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order item not found",
        )

    if order_item.product_id != payload.product_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The order item does not belong to this product",
        )

    order = (
        db.query(Order)
        .filter(
            Order.id == order_item.order_id,
            Order.user_id == current_user.id,
        )
        .first()
    )

    if order is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only review products from your own orders",
        )

    if not _is_order_delivered(db, order):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can review a product only after delivery",
        )

    review = ProductReview(
        product_id=payload.product_id,
        user_id=current_user.id,
        order_item_id=payload.order_item_id,
        rating=payload.rating,
        title=payload.title,
        review_text=payload.review_text,
        status="PENDING",
    )

    db.add(review)

    try:
        db.commit()
        db.refresh(review)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already reviewed this product",
        )

    return review


@router.get(
    "/products/{product_id}",
    response_model=list[ProductReviewResponse],
)
def get_product_reviews(
    product_id: int,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    product_exists = (
        db.query(Product.id)
        .filter(Product.id == product_id)
        .first()
    )

    if product_exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    return (
        db.query(ProductReview)
        .filter(
            ProductReview.product_id == product_id,
            ProductReview.status == "APPROVED",
        )
        .order_by(ProductReview.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get(
    "/products/{product_id}/summary",
)
def get_product_review_summary(
    product_id: int,
    db: Session = Depends(get_db),
):
    product = (
        db.query(Product.id)
        .filter(Product.id == product_id)
        .first()
    )

    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    summary = (
        db.query(
            func.count(ProductReview.id).label("review_count"),
            func.avg(ProductReview.rating).label("average_rating"),
        )
        .filter(
            ProductReview.product_id == product_id,
            ProductReview.status == "APPROVED",
        )
        .first()
    )

    return {
        "product_id": product_id,
        "review_count": summary.review_count or 0,
        "average_rating": (
            round(float(summary.average_rating), 2)
            if summary.average_rating is not None
            else None
        ),
    }


@router.get(
    "/mine",
    response_model=list[ProductReviewResponse],
)
def get_my_reviews(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(ProductReview)
        .filter(ProductReview.user_id == current_user.id)
        .order_by(ProductReview.created_at.desc())
        .all()
    )


@router.get(
    "",
    response_model=list[ProductReviewResponse],
)
def list_reviews_for_moderation(
    review_status: str = Query(
        default="PENDING",
        pattern=r"^(PENDING|APPROVED|REJECTED|ALL)$",
    ),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*STAFF_ROLES)),
):
    query = db.query(ProductReview)

    if review_status != "ALL":
        query = query.filter(ProductReview.status == review_status)

    return (
        query.order_by(ProductReview.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.patch(
    "/{review_id}/moderation",
    response_model=ProductReviewResponse,
)
def moderate_product_review(
    review_id: int,
    payload: ReviewModerationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*STAFF_ROLES)),
):
    review = (
        db.query(ProductReview)
        .filter(ProductReview.id == review_id)
        .first()
    )

    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found",
        )

    if review.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only pending reviews can be moderated",
        )

    review.status = payload.status
    review.moderation_note = payload.moderation_note
    review.moderated_by = current_user.id
    review.moderated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(review)

    return review
