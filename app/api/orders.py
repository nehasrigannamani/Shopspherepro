from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.order import Order, OrderItem
from app.models.user import User, UserRole
from app.schemas.order import OrderDetailResponse, OrderResponse

router = APIRouter(prefix="/orders", tags=["Order Management"])

STAFF_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.ORDER_MANAGER,
    UserRole.FINANCE_MANAGER,
}


def get_order_or_404(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def build_order_detail(db: Session, order: Order) -> OrderDetailResponse:
    items = list(
        db.scalars(
            select(OrderItem)
            .where(OrderItem.order_id == order.id)
            .order_by(OrderItem.id)
        ).all()
    )

    order_data = {
        column.name: getattr(order, column.name)
        for column in Order.__table__.columns
    }

    return OrderDetailResponse(**order_data, items=items)


def apply_filters(
    query,
    *,
    order_status,
    payment_status,
    order_number,
    date_from,
    date_to,
):
    if order_status:
        query = query.where(Order.status == order_status.upper())
    if payment_status:
        query = query.where(Order.payment_status == payment_status.upper())
    if order_number:
        query = query.where(Order.order_number.like(f"%{order_number.strip().upper()}%"))
    if date_from:
        query = query.where(Order.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        query = query.where(
            Order.created_at < datetime.combine(date_to + timedelta(days=1), time.min)
        )
    return query


@router.get("", response_model=list[OrderResponse])
def list_my_orders(
    status: str | None = Query(default=None, max_length=30),
    payment_status: str | None = Query(default=None, max_length=30),
    order_number: str | None = Query(default=None, max_length=40),
    date_from: date | None = None,
    date_to: date | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = apply_filters(
        select(Order).where(Order.user_id == current_user.id),
        order_status=status,
        payment_status=payment_status,
        order_number=order_number,
        date_from=date_from,
        date_to=date_to,
    )
    query = query.order_by(Order.created_at.desc(), Order.id.desc()).offset(skip).limit(limit)
    return list(db.scalars(query).all())


@router.get("/admin/all", response_model=list[OrderResponse])
def list_all_orders(
    status: str | None = Query(default=None, max_length=30),
    payment_status: str | None = Query(default=None, max_length=30),
    order_number: str | None = Query(default=None, max_length=40),
    user_id: int | None = Query(default=None, gt=0),
    date_from: date | None = None,
    date_to: date | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in STAFF_ROLES:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to view all orders",
        )

    query = select(Order)
    if user_id is not None:
        query = query.where(Order.user_id == user_id)
    query = apply_filters(
        query,
        order_status=status,
        payment_status=payment_status,
        order_number=order_number,
        date_from=date_from,
        date_to=date_to,
    )
    query = query.order_by(Order.created_at.desc(), Order.id.desc()).offset(skip).limit(limit)
    return list(db.scalars(query).all())


@router.get("/{order_id}", response_model=OrderDetailResponse)
def get_order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = get_order_or_404(db, order_id)

    if (
        order.user_id != current_user.id
        and current_user.role not in STAFF_ROLES
    ):
        # Do not reveal whether another customer's order exists.
        raise HTTPException(status_code=404, detail="Order not found")

    return build_order_detail(db, order)