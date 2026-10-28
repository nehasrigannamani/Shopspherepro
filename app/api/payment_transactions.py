
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.order import Order
from app.models.payment import Payment
from app.models.payment_transaction import PaymentTransaction
from app.models.user import User, UserRole
from app.schemas.payments import PaymentTransactionResponse

router = APIRouter(tags=["Payment Transaction History"])

STAFF_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.FINANCE_MANAGER,
}


@router.get(
    "/payments/{payment_id}/transactions",
    response_model=list[PaymentTransactionResponse],
)
def list_payment_transactions(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payment = db.query(Payment).filter(
        Payment.id == payment_id
    ).first()

    if payment is None or (
        payment.user_id != current_user.id
        and current_user.role not in STAFF_ROLES
    ):
        raise HTTPException(
            status_code=404,
            detail="Payment not found",
        )

    return (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.payment_id == payment_id)
        .order_by(
            PaymentTransaction.created_at.asc(),
            PaymentTransaction.id.asc(),
        )
        .all()
    )


@router.get(
    "/orders/{order_id}/payment-transactions",
    response_model=list[PaymentTransactionResponse],
)
def list_order_payment_transactions(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = db.query(Order).filter(
        Order.id == order_id
    ).first()

    if order is None or (
        order.user_id != current_user.id
        and current_user.role not in STAFF_ROLES
    ):
        raise HTTPException(
            status_code=404,
            detail="Order not found",
        )

    payments = db.query(Payment.id).filter(
        Payment.order_id == order_id
    ).all()

    payment_ids = [payment_id for (payment_id,) in payments]

    if not payment_ids:
        return []

    return (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.payment_id.in_(payment_ids))
        .order_by(
            PaymentTransaction.created_at.asc(),
            PaymentTransaction.id.asc(),
        )
        .all()
    )
