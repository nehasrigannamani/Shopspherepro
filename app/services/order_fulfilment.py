"""Order fulfilment: status machine, split shipments, back-orders, item cancellation.

Functions add/flush only; callers commit (so each business action is one
transaction). Callers must already hold a row lock on the order
(``SELECT ... FOR UPDATE``) - that serialises concurrent fulfilment actions.
"""
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import Inventory, InventoryMovement
from app.models.order import Order, OrderItem
from app.models.order_fulfilment import (
    OrderBackorder, OrderItemCancellation, OrderShipment, OrderShipmentItem,
)
from app.models.order_status_history import OrderStatusHistory
from app.models.payment import Payment
from app.models.refund import Refund
from app.models.user import UserRole
from app.models.variant_inventory import VariantInventory, VariantInventoryMovement
from app.services.loyalty import credit_order_points
from app.services.notify import create_notification, notify_roles
from app.services.order_benefits import reverse_order_benefits
from app.services.payments import PAID_STATUSES, committed_refund_total
from app.services.pricing import money
from app.services.reservations import now_naive

# ---------------------------------------------------------------- status machine
TRANSITIONS_V2: dict[str, set[str]] = {
    "PLACED": {"CONFIRMED", "CANCELLED"},
    "PENDING": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"PROCESSING", "CANCELLED"},
    "PROCESSING": {"PARTIALLY_SHIPPED", "SHIPPED", "BACKORDERED", "CANCELLED"},
    "BACKORDERED": {"PROCESSING", "PARTIALLY_SHIPPED", "SHIPPED", "CANCELLED"},
    "PARTIALLY_SHIPPED": {"SHIPPED", "DELIVERED"},
    "SHIPPED": {"DELIVERED"},
    "DELIVERED": set(),
    "CANCELLED": set(),
}
# Statuses staff may set by hand; everything else is derived from item quantities.
MANUAL_TARGETS = {"CONFIRMED", "PROCESSING"}
SHIPPABLE_STATUSES = {"CONFIRMED", "PROCESSING", "PARTIALLY_SHIPPED", "BACKORDERED"}
CUSTOMER_CANCELLABLE = {"PLACED", "PENDING", "CONFIRMED"}
STAFF_CANCELLABLE = CUSTOMER_CANCELLABLE | {"PROCESSING", "PARTIALLY_SHIPPED", "BACKORDERED"}
EDITABLE_STATUSES = {"PLACED", "PENDING", "CONFIRMED", "PROCESSING", "BACKORDERED"}


def status_of(order: Order) -> str:
    return str(order.status).upper()


def transition(db: Session, order: Order, new_status: str, actor_id: int | None, reason: str | None) -> None:
    old = status_of(order)
    if old == new_status:
        return
    if new_status not in TRANSITIONS_V2.get(old, set()):
        raise HTTPException(status_code=409, detail=f"Invalid status transition: {old} -> {new_status}")
    order.status = new_status
    db.add(OrderStatusHistory(
        order_id=order.id, previous_status=old, new_status=new_status,
        changed_by=actor_id, note=reason,
    ))


def lock_items(db: Session, order_id: int) -> list[OrderItem]:
    return list(db.scalars(
        select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id).with_for_update()
    ).all())


def active_qty(item: OrderItem) -> int:
    return item.quantity - item.quantity_cancelled


def shippable_qty(item: OrderItem) -> int:
    """Units that can be shipped (or back-ordered) right now."""
    return item.quantity - item.quantity_cancelled - item.quantity_shipped - item.quantity_backordered


def refresh_item_status(item: OrderItem) -> None:
    active = active_qty(item)
    if active == 0:
        item.fulfillment_status = "CANCELLED"
    elif item.quantity_delivered == active:
        item.fulfillment_status = "DELIVERED"
    elif item.quantity_shipped == active:
        item.fulfillment_status = "SHIPPED"
    elif item.quantity_shipped > 0:
        item.fulfillment_status = "PARTIALLY_SHIPPED"
    elif item.quantity_backordered >= active:
        item.fulfillment_status = "BACKORDERED"
    else:
        item.fulfillment_status = "PENDING"


def recompute_order_status(db: Session, order: Order, items: list[OrderItem], actor_id: int | None, reason: str) -> str:
    """Derive the order status from item quantities and apply it through the machine."""
    for it in items:
        refresh_item_status(it)
    active = sum(active_qty(i) for i in items)
    shipped = sum(i.quantity_shipped for i in items)
    delivered = sum(i.quantity_delivered for i in items)
    backordered = sum(i.quantity_backordered for i in items)
    cur = status_of(order)

    if active == 0:
        target = "CANCELLED"
    elif delivered == active:
        target = "DELIVERED"
    elif shipped == active:
        target = "SHIPPED"
    elif shipped > 0:
        target = "PARTIALLY_SHIPPED"
    elif backordered >= active:
        target = "BACKORDERED"
    elif cur == "BACKORDERED":
        target = "PROCESSING"
    else:
        target = cur

    if target != cur:
        if cur == "CONFIRMED" and target not in ("CANCELLED", "PROCESSING"):
            transition(db, order, "PROCESSING", actor_id, reason)
        transition(db, order, target, actor_id, reason)
        if target == "DELIVERED":
            credit_order_points(db, order, actor_id)
    return status_of(order)


# ---------------------------------------------------------------- shipments
def _by_id(items: list[OrderItem]) -> dict[int, OrderItem]:
    return {i.id: i for i in items}


def _merge_lines(lines: list[tuple[int, int]]) -> dict[int, int]:
    merged: dict[int, int] = {}
    for item_id, qty in lines:
        merged[item_id] = merged.get(item_id, 0) + qty
    return merged


def create_shipment(db: Session, order: Order, lines, *, carrier_name, tracking_number, estimated_delivery_at,
                    notes, actor_id) -> OrderShipment:
    if status_of(order) not in SHIPPABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"Order in status {status_of(order)} cannot be shipped")
    items = lock_items(db, order.id)
    index = _by_id(items)
    wanted = _merge_lines(lines)
    for item_id, qty in wanted.items():
        item = index.get(item_id)
        if item is None:
            raise HTTPException(status_code=422, detail=f"Item {item_id} is not part of this order")
        room = shippable_qty(item)
        if qty > room:
            raise HTTPException(
                status_code=409,
                detail=f"Only {room} unit(s) of item {item_id} can be shipped now",
            )

    count = db.query(OrderShipment).filter(OrderShipment.order_id == order.id).count()
    shipment = OrderShipment(
        order_id=order.id,
        shipment_number=f"{order.order_number}-S{count + 1}",
        carrier_name=carrier_name, tracking_number=tracking_number,
        estimated_delivery_at=estimated_delivery_at, notes=notes,
        status="SHIPPED", shipped_at=now_naive(), created_by=actor_id,
    )
    db.add(shipment)
    db.flush()
    for item_id, qty in wanted.items():
        db.add(OrderShipmentItem(shipment_id=shipment.id, order_item_id=item_id, quantity=qty))
        index[item_id].quantity_shipped += qty

    new_status = recompute_order_status(db, order, items, actor_id, f"Shipment {shipment.shipment_number} shipped")
    partial = new_status == "PARTIALLY_SHIPPED"
    create_notification(
        db, order.user_id,
        f"Order {order.order_number}: {'part of your order' if partial else 'your order'} has shipped",
        f"Shipment {shipment.shipment_number} is on its way with {carrier_name}."
        + (f" Tracking: {tracking_number}." if tracking_number else ""),
        notification_type="SHIPMENT", reference_type="order", reference_id=order.id,
        email=True, sms=True,
    )
    return shipment


def deliver_shipment(db: Session, order: Order, shipment: OrderShipment, actor_id: int | None) -> OrderShipment:
    if shipment.status != "SHIPPED":
        raise HTTPException(status_code=409, detail=f"Shipment is already {shipment.status}")
    items = lock_items(db, order.id)
    index = _by_id(items)
    rows = db.scalars(select(OrderShipmentItem).where(OrderShipmentItem.shipment_id == shipment.id)).all()
    for row in rows:
        item = index[row.order_item_id]
        item.quantity_delivered += row.quantity
        if item.quantity_delivered > item.quantity_shipped:  # defensive: should be impossible
            raise HTTPException(status_code=409, detail="Delivered quantity would exceed shipped quantity")
    shipment.status = "DELIVERED"
    shipment.delivered_at = now_naive()
    new_status = recompute_order_status(db, order, items, actor_id, f"Shipment {shipment.shipment_number} delivered")
    create_notification(
        db, order.user_id, f"Order {order.order_number}: shipment delivered",
        f"Shipment {shipment.shipment_number} was delivered."
        + (" Your order is complete." if new_status == "DELIVERED" else ""),
        notification_type="SHIPMENT", reference_type="order", reference_id=order.id, email=True,
    )
    return shipment


# ---------------------------------------------------------------- back-orders
def create_backorder(db: Session, order: Order, item_id: int, quantity: int, expected_at, reason, actor_id) -> OrderBackorder:
    if status_of(order) not in SHIPPABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"Order in status {status_of(order)} cannot be back-ordered")
    items = lock_items(db, order.id)
    item = _by_id(items).get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Order item not found")
    room = shippable_qty(item)
    if quantity > room:
        raise HTTPException(status_code=409, detail=f"Only {room} unit(s) of item {item_id} can be back-ordered")
    backorder = OrderBackorder(
        order_id=order.id, order_item_id=item.id, quantity=quantity, status="OPEN",
        expected_at=expected_at, reason=reason, created_by=actor_id,
    )
    db.add(backorder)
    item.quantity_backordered += quantity
    db.flush()
    recompute_order_status(db, order, items, actor_id, f"Back-order opened for item {item.id}")
    create_notification(
        db, order.user_id, f"Order {order.order_number}: item delayed",
        f"{quantity} unit(s) of {item.product_name} are back-ordered"
        + (f" and expected by {expected_at:%Y-%m-%d}." if expected_at else "."),
        notification_type="ORDER", reference_type="order", reference_id=order.id, email=True,
    )
    return backorder


def resolve_backorder(db: Session, order: Order, backorder: OrderBackorder, actor_id: int | None) -> OrderBackorder:
    if backorder.status != "OPEN":
        raise HTTPException(status_code=409, detail=f"Back-order is already {backorder.status}")
    items = lock_items(db, order.id)
    item = _by_id(items)[backorder.order_item_id]
    item.quantity_backordered -= backorder.quantity
    backorder.status = "RESOLVED"
    backorder.resolved_by = actor_id
    backorder.resolved_at = now_naive()
    recompute_order_status(db, order, items, actor_id, f"Back-order {backorder.id} resolved")
    return backorder


def close_open_backorders(db: Session, order_id: int, actor_id: int | None) -> None:
    """Used when a whole order is cancelled (Phase 1 cancel endpoint)."""
    for bo in db.scalars(select(OrderBackorder).where(
            OrderBackorder.order_id == order_id, OrderBackorder.status == "OPEN")).all():
        bo.status = "CANCELLED"
        bo.resolved_by = actor_id
        bo.resolved_at = now_naive()


# ---------------------------------------------------------------- item cancellation
def restore_stock(db: Session, item: OrderItem, quantity: int, reason: str, actor_id: int | None) -> None:
    if item.variant_id is not None:
        stock = db.scalar(select(VariantInventory).where(VariantInventory.variant_id == item.variant_id).with_for_update())
        movement, key = VariantInventoryMovement, "variant_inventory_id"
    else:
        stock = db.scalar(select(Inventory).where(Inventory.product_id == item.product_id).with_for_update())
        movement, key = InventoryMovement, "inventory_id"
    if stock is None:
        raise HTTPException(status_code=409, detail=f"Inventory record is missing for product {item.product_id}")
    stock.quantity_on_hand += quantity
    db.add(movement(**{key: stock.id}, quantity_change=quantity, reason=reason[:255], performed_by=actor_id))


def cancel_items(db: Session, order: Order, lines, reason: str, actor_id: int, is_staff: bool) -> dict:
    status = status_of(order)
    allowed = STAFF_CANCELLABLE if is_staff else CUSTOMER_CANCELLABLE
    if status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Items cannot be cancelled while the order is {status}; use the return/refund workflow"
            if status in {"SHIPPED", "DELIVERED", "PARTIALLY_SHIPPED"} or status == "CANCELLED"
            else f"Orders in status {status} cannot be changed by you",
        )
    items = lock_items(db, order.id)
    index = _by_id(items)
    wanted = _merge_lines(lines)

    for item_id, qty in wanted.items():
        item = index.get(item_id)
        if item is None:
            raise HTTPException(status_code=422, detail=f"Item {item_id} is not part of this order")
        unshipped = item.quantity - item.quantity_cancelled - item.quantity_shipped
        if qty > unshipped:
            raise HTTPException(
                status_code=409, detail=f"Only {unshipped} unit(s) of item {item_id} can still be cancelled")

    remaining_after = sum(
        active_qty(i) - wanted.get(i.id, 0) for i in items
    )
    full_cancel = remaining_after == 0
    pay_state = str(order.payment_status).upper()
    if not full_cancel and pay_state in {"PENDING", "FAILED", "CANCELLED"}:
        raise HTTPException(
            status_code=409,
            detail="Partial cancellation needs a paid order; pay the order or cancel it entirely",
        )

    total_refund = Decimal("0.00")
    records: list[OrderItemCancellation] = []
    for item_id, qty in wanted.items():
        item = index[item_id]
        free = item.quantity - item.quantity_cancelled - item.quantity_shipped - item.quantity_backordered
        from_backorder = max(0, qty - free)
        if from_backorder:
            for bo in db.scalars(select(OrderBackorder).where(
                    OrderBackorder.order_item_id == item.id, OrderBackorder.status == "OPEN"
            ).order_by(OrderBackorder.id).with_for_update()).all():
                if from_backorder == 0:
                    break
                take = min(bo.quantity, from_backorder)
                bo.quantity -= take
                if bo.quantity == 0:
                    bo.status = "CANCELLED"
                    bo.resolved_by = actor_id
                    bo.resolved_at = now_naive()
                item.quantity_backordered -= take
                from_backorder -= take

        before = money(Decimal(item.line_total) * item.quantity_cancelled / item.quantity)
        item.quantity_cancelled += qty
        after = money(Decimal(item.line_total) * item.quantity_cancelled / item.quantity)
        amount = after - before
        total_refund += amount
        restore_stock(db, item, qty, f"Order {order.order_number} item cancelled: {reason}", actor_id)
        rec = OrderItemCancellation(
            order_id=order.id, order_item_id=item.id, quantity=qty,
            refund_amount=amount, reason=reason, cancelled_by=actor_id,
        )
        db.add(rec)
        records.append(rec)
    db.flush()

    refund_id = None
    refund_note = None
    paid_payment = db.scalar(
        select(Payment).where(Payment.order_id == order.id, Payment.status.in_(PAID_STATUSES)).with_for_update()
    )
    if paid_payment is not None:
        refundable = Decimal(str(paid_payment.amount)) - committed_refund_total(db, paid_payment.id)
        amount = refundable if full_cancel else min(total_refund, refundable)
        if amount > 0:
            refund = Refund(
                payment_id=paid_payment.id, order_id=order.id, requested_by=actor_id, processed_by=actor_id,
                amount=amount, currency=paid_payment.currency,
                reason=f"Items cancelled: {reason}", status="APPROVED",
                decision_note="Auto-approved: cancellation before shipment",
            )
            db.add(refund)
            db.flush()
            refund_id = refund.id
            for rec in records:
                rec.refund_id = refund_id
    elif pay_state == "PAID":
        refund_note = "Payment was collected outside the gateway; finance must refund it manually"
        notify_roles(
            db, (UserRole.FINANCE_MANAGER,), "Manual refund required",
            f"Items of order {order.order_number} were cancelled after payment was collected outside the gateway.",
            notification_type="REFUND", reference_type="order", reference_id=order.id,
        )
    if full_cancel:
        for p in db.scalars(select(Payment).where(
                Payment.order_id == order.id, Payment.status == "PENDING").with_for_update()).all():
            p.status = "CANCELLED"

    new_status = recompute_order_status(db, order, items, actor_id, reason)
    if new_status == "CANCELLED":
        reverse_order_benefits(db, order)
    create_notification(
        db, order.user_id, f"Order {order.order_number} updated",
        ("Your order was cancelled." if new_status == "CANCELLED" else "Some items were cancelled.")
        + (" A refund has been started." if refund_id else ""),
        notification_type="ORDER", reference_type="order", reference_id=order.id, email=True,
    )
    return {
        "records": records, "refund_id": refund_id, "refund_note": refund_note,
        "refund_amount": total_refund, "order_status": new_status,
    }


# ---------------------------------------------------------------- v1 compatibility
def sync_items_with_order_status(db: Session, order: Order, new_status: str) -> None:
    """Phase 1 ``PATCH /orders/{id}/status`` ships/delivers a whole order. Keep the
    item counters consistent with that."""
    if new_status not in {"SHIPPED", "DELIVERED"}:
        return
    for item in lock_items(db, order.id):
        active = active_qty(item)
        item.quantity_shipped = active
        if new_status == "DELIVERED":
            item.quantity_delivered = active
        refresh_item_status(item)
