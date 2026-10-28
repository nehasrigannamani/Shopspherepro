
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.api.dependencies import get_current_user, require_roles
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem
from app.models.supplier import Supplier
from app.models.product import Product
from app.schemas.purchase_orders import (
    PurchaseOrderCreate,
    PurchaseOrderUpdate,
    PurchaseOrderStatusUpdate,
    PurchaseOrderResponse,
    PurchaseOrderItemReceive,
)

router = APIRouter(
    prefix="/purchase-orders",
    tags=["Purchase Order Management"],
)

PURCHASE_ORDER_ROLES = (
    "SUPER_ADMIN",
    "ADMIN",
    "INVENTORY_MANAGER",
    "FINANCE_MANAGER",
)

STATUS_TRANSITIONS = {
    "DRAFT": {"SUBMITTED", "CANCELLED"},
    "SUBMITTED": {"APPROVED", "CANCELLED"},
    "APPROVED": {"ORDERED", "CANCELLED"},
    "ORDERED": {"CANCELLED"},
    "PARTIALLY_RECEIVED": set(),
    "RECEIVED": set(),
    "CANCELLED": set(),
}


def load_purchase_order(db: Session, purchase_order_id: int):
    return (
        db.query(PurchaseOrder)
        .options(selectinload(PurchaseOrder.items))
        .filter(PurchaseOrder.id == purchase_order_id)
        .first()
    )


def get_purchase_order_or_404(
    db: Session,
    purchase_order_id: int,
):
    purchase_order = load_purchase_order(db, purchase_order_id)

    if purchase_order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Purchase order not found.",
        )

    return purchase_order


def calculate_total(items):
    return sum(
        (
            Decimal(str(item.quantity_ordered))
            * Decimal(str(item.unit_cost))
            for item in items
        ),
        Decimal("0.00"),
    )


@router.post(
    "",
    response_model=PurchaseOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_purchase_order(
    payload: PurchaseOrderCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*PURCHASE_ORDER_ROLES)),
):
    supplier = (
        db.query(Supplier)
        .filter(Supplier.id == payload.supplier_id)
        .first()
    )

    if supplier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found.",
        )

    if not supplier.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create a purchase order for an inactive supplier.",
        )

    if (
        payload.expected_delivery_date is not None
        and payload.expected_delivery_date < date.today()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expected delivery date cannot be in the past.",
        )

    for item in payload.items:
        product_exists = (
            db.query(Product.id)
            .filter(Product.id == item.product_id)
            .first()
        )

        if product_exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {item.product_id} was not found.",
            )

    po_number = (
        f"PO-{datetime.now().strftime('%Y%m%d%H%M%S')}-"
        f"{uuid4().hex[:6].upper()}"
    )

    purchase_order = PurchaseOrder(
        po_number=po_number,
        supplier_id=payload.supplier_id,
        status="DRAFT",
        currency=payload.currency.upper(),
        expected_delivery_date=payload.expected_delivery_date,
        notes=payload.notes,
        total_amount=Decimal("0.00"),
        created_by=current_user.id,
    )

    try:
        db.add(purchase_order)
        db.flush()

        for item in payload.items:
            line_total = (
                Decimal(str(item.quantity_ordered))
                * item.unit_cost
            )

            db.add(
                PurchaseOrderItem(
                    purchase_order_id=purchase_order.id,
                    product_id=item.product_id,
                    quantity_ordered=item.quantity_ordered,
                    quantity_received=0,
                    unit_cost=item.unit_cost,
                    line_total=line_total,
                )
            )

        purchase_order.total_amount = calculate_total(payload.items)
        db.commit()

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The purchase order could not be saved due to a data conflict.",
        )

    return get_purchase_order_or_404(db, purchase_order.id)


@router.get("", response_model=list[PurchaseOrderResponse])
def list_purchase_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    supplier_id: int | None = Query(default=None, gt=0),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*PURCHASE_ORDER_ROLES)),
):
    query = db.query(PurchaseOrder).options(
        selectinload(PurchaseOrder.items)
    )

    if status_filter:
        query = query.filter(
            PurchaseOrder.status == status_filter.upper()
        )

    if supplier_id is not None:
        query = query.filter(
            PurchaseOrder.supplier_id == supplier_id
        )

    return (
        query.order_by(PurchaseOrder.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/{purchase_order_id}", response_model=PurchaseOrderResponse)
def get_purchase_order(
    purchase_order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*PURCHASE_ORDER_ROLES)),
):
    return get_purchase_order_or_404(db, purchase_order_id)


@router.patch(
    "/{purchase_order_id}",
    response_model=PurchaseOrderResponse,
)
def update_purchase_order(
    purchase_order_id: int,
    payload: PurchaseOrderUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*PURCHASE_ORDER_ROLES)),
):
    purchase_order = get_purchase_order_or_404(
        db, purchase_order_id
    )

    if purchase_order.status != "DRAFT":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only draft purchase orders can be edited.",
        )

    changes = payload.model_dump(exclude_unset=True)

    if (
        "expected_delivery_date" in changes
        and changes["expected_delivery_date"] is not None
        and changes["expected_delivery_date"] < date.today()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expected delivery date cannot be in the past.",
        )

    for field, value in changes.items():
        setattr(purchase_order, field, value)

    db.commit()
    return get_purchase_order_or_404(db, purchase_order_id)


@router.patch(
    "/{purchase_order_id}/status",
    response_model=PurchaseOrderResponse,
)
def update_purchase_order_status(
    purchase_order_id: int,
    payload: PurchaseOrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*PURCHASE_ORDER_ROLES)),
):
    purchase_order = get_purchase_order_or_404(
        db, purchase_order_id
    )

    new_status = payload.status.upper()
    old_status = purchase_order.status

    if new_status not in STATUS_TRANSITIONS.get(old_status, set()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid purchase order status transition: {old_status} to {new_status}.",
        )

    if new_status == "CANCELLED":
        if any(item.quantity_received > 0 for item in purchase_order.items):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A purchase order with received quantities cannot be cancelled.",
            )

    if new_status == "APPROVED":
        purchase_order.approved_by = current_user.id

    if new_status == "ORDERED":
        purchase_order.ordered_at = datetime.utcnow()

    purchase_order.status = new_status
    db.commit()

    return get_purchase_order_or_404(db, purchase_order_id)


@router.post(
    "/{purchase_order_id}/items/{item_id}/receive",
    response_model=PurchaseOrderResponse,
)
def receive_purchase_order_item(
    purchase_order_id: int,
    item_id: int,
    payload: PurchaseOrderItemReceive,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(*PURCHASE_ORDER_ROLES)),
):
    purchase_order = get_purchase_order_or_404(
        db, purchase_order_id
    )

    if purchase_order.status not in {"ORDERED", "PARTIALLY_RECEIVED"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Items can only be received after the purchase order is ordered.",
        )

    item = next(
        (
            order_item
            for order_item in purchase_order.items
            if order_item.id == item_id
        ),
        None,
    )

    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Purchase order item not found.",
        )

    remaining_quantity = (
        item.quantity_ordered - item.quantity_received
    )

    if payload.quantity_received > remaining_quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only {remaining_quantity} additional units can be received for this item.",
        )

    item.quantity_received += payload.quantity_received

    all_items_received = all(
        order_item.quantity_received == order_item.quantity_ordered
        for order_item in purchase_order.items
    )

    any_items_received = any(
        order_item.quantity_received > 0
        for order_item in purchase_order.items
    )

    if all_items_received:
        purchase_order.status = "RECEIVED"
        purchase_order.received_at = datetime.utcnow()
    elif any_items_received:
        purchase_order.status = "PARTIALLY_RECEIVED"

    db.commit()

    return get_purchase_order_or_404(db, purchase_order_id)
