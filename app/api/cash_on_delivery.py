
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.order import Order
from app.models.user import User, UserRole

router = APIRouter(prefix="/cod", tags=["Cash on Delivery"])

STAFF_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.ORDER_MANAGER,
    UserRole.FINANCE_MANAGER,
}


def role_value(user: User) -> str:
    role = user.role
    return str(getattr(role, "value", role))


class CODConfirmationResponse(BaseModel):
    order_id: int
    order_number: str
    payment_method: str
    payment_status: str
    message: str


class CODCollectionRequest(BaseModel):
    collected_amount: float = Field(gt=0)
    collection_reference: str | None = Field(
        default=None, max_length=100
    )


@router.post(
    "/orders/{order_id}/confirm",
    response_model=CODConfirmationResponse,
)
def confirm_cod_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .with_for_update()
        .first()
    )

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You cannot select COD for another customer's order",
        )

    if str(order.status).upper() not in {"PLACED", "PENDING"}:
        raise HTTPException(
            status_code=400,
            detail="COD can only be selected for a newly placed order",
        )

    if str(order.payment_status).upper() not in {
        "PENDING",
        "UNPAID",
    }:
        raise HTTPException(
            status_code=400,
            detail="COD is only available for unpaid orders",
        )

    if getattr(order, "payment_method", None) not in (None, "", "COD"):
        raise HTTPException(
            status_code=400,
            detail="A different payment method has already been selected",
        )

    if hasattr(order, "payment_method"):
        order.payment_method = "COD"

    try:
        db.commit()
        db.refresh(order)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not confirm COD selection",
        )

    return CODConfirmationResponse(
        order_id=order.id,
        order_number=order.order_number,
        payment_method="COD",
        payment_status=order.payment_status,
        message="Cash on Delivery selected. Payment remains unpaid until collection.",
    )


@router.post("/orders/{order_id}/collect")
def record_cod_collection(
    order_id: int,
    payload: CODCollectionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if role_value(current_user) not in {
        role.value for role in STAFF_ROLES
    }:
        raise HTTPException(
            status_code=403,
            detail="Only authorized staff can record COD collection",
        )

    order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .with_for_update()
        .first()
    )

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if getattr(order, "payment_method", None) != "COD":
        raise HTTPException(
            status_code=400,
            detail="This order is not a COD order",
        )

    if str(order.status).upper() != "DELIVERED":
        raise HTTPException(
            status_code=400,
            detail="COD collection can only be recorded for delivered orders",
        )

    if str(order.payment_status).upper() in {
        "PAID",
        "SUCCEEDED",
        "COMPLETED",
    }:
        raise HTTPException(
            status_code=400,
            detail="Payment has already been recorded",
        )

    if payload.collected_amount != float(order.grand_total):
        raise HTTPException(
            status_code=400,
            detail="Collected amount must match the order total",
        )

    order.payment_status = "PAID"

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not record COD collection",
        )

    return {
        "message": "COD collection recorded",
        "order_id": order.id,
        "payment_status": order.payment_status,
        "collection_reference": payload.collection_reference,
    }
