from datetime import timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.core.ratelimit import rate_limit
from app.db.session import get_db
from app.models.order import Order, OrderItem
from app.models.order_fulfilment import (
    OrderBackorder, OrderItemCancellation, OrderShipment, OrderShipmentItem,
)
from app.models.order_status_history import OrderStatusHistory
from app.models.user import User, UserRole
from app.schemas.order_fulfilment import (
    BackorderCreate, BackorderOut, CancelItemsRequest, CancelItemsResponse, CancelledLine,
    FulfilmentItem, FulfilmentResponse, FulfilmentTotals, OrderAdminUpdate,
    OrderAdminUpdateResponse, ShipmentCreate, ShipmentOut, StatusChange, TimelineEvent,
)
from app.services import idempotency
from app.services import order_fulfilment as svc
from app.services.audit import write_audit_log
from app.services.notify import create_notification
from app.services.payments import RefundError, process_refund

router = APIRouter(prefix="/v2/orders", tags=["Orders (v2)"])

R = UserRole
READ_STAFF = (R.SUPER_ADMIN, R.ADMIN, R.ORDER_MANAGER, R.DELIVERY_MANAGER, R.SUPPORT_AGENT,
              R.FINANCE_MANAGER, R.INVENTORY_MANAGER)
SHIP_STAFF = (R.SUPER_ADMIN, R.ADMIN, R.ORDER_MANAGER, R.DELIVERY_MANAGER)
BACKORDER_STAFF = (R.SUPER_ADMIN, R.ADMIN, R.ORDER_MANAGER, R.INVENTORY_MANAGER)
ORDER_STAFF = (R.SUPER_ADMIN, R.ADMIN, R.ORDER_MANAGER)

IDEM = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=128,
              description="Optional. Makes retries safe: same key + body replays the first response.")


def _load_order(db: Session, order_id: int, user: User, *, lock: bool = False) -> Order:
    stmt = select(Order).where(Order.id == order_id)
    if lock:
        stmt = stmt.with_for_update()
    order = db.scalar(stmt)
    # Another customer's order is reported as missing, never as forbidden.
    if order is None or (user.role == R.CUSTOMER and order.user_id != user.id):
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _naive(dt):
    """Aware datetimes are converted to UTC; naive ones are taken as UTC (how the DB stores them)."""
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _shipment_out(db: Session, shipment: OrderShipment) -> ShipmentOut:
    rows = db.scalars(select(OrderShipmentItem).where(
        OrderShipmentItem.shipment_id == shipment.id).order_by(OrderShipmentItem.id)).all()
    return ShipmentOut(
        id=shipment.id, order_id=shipment.order_id, shipment_number=shipment.shipment_number,
        carrier_name=shipment.carrier_name, tracking_number=shipment.tracking_number,
        status=shipment.status, estimated_delivery_at=shipment.estimated_delivery_at,
        shipped_at=shipment.shipped_at, delivered_at=shipment.delivered_at, notes=shipment.notes,
        items=[{"order_item_id": r.order_item_id, "quantity": r.quantity} for r in rows],
    )


# ------------------------------------------------------------------ reads
@router.get("/{order_id}/fulfilment", response_model=FulfilmentResponse,
            summary="Fulfilment details: item counters, shipments, back-orders")
def fulfilment(order_id: int, db: Session = Depends(get_db),
               user: User = Depends(require_roles(R.CUSTOMER, *READ_STAFF))):
    order = _load_order(db, order_id, user)
    items = list(db.scalars(select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id)).all())
    shipments = db.scalars(select(OrderShipment).where(
        OrderShipment.order_id == order.id).order_by(OrderShipment.id)).all()
    backorders = db.scalars(select(OrderBackorder).where(
        OrderBackorder.order_id == order.id).order_by(OrderBackorder.id)).all()
    totals = FulfilmentTotals(
        ordered=sum(i.quantity for i in items), cancelled=sum(i.quantity_cancelled for i in items),
        shipped=sum(i.quantity_shipped for i in items), delivered=sum(i.quantity_delivered for i in items),
        backordered=sum(i.quantity_backordered for i in items),
        remaining_to_ship=sum(svc.shippable_qty(i) for i in items),
    )
    return FulfilmentResponse(
        order_id=order.id, order_number=order.order_number, status=svc.status_of(order),
        payment_status=str(order.payment_status), totals=totals,
        items=[FulfilmentItem(
            order_item_id=i.id, product_id=i.product_id, product_name=i.product_name, sku=i.product_sku,
            ordered=i.quantity, cancelled=i.quantity_cancelled, shipped=i.quantity_shipped,
            delivered=i.quantity_delivered, backordered=i.quantity_backordered,
            shippable_now=svc.shippable_qty(i), fulfillment_status=i.fulfillment_status) for i in items],
        shipments=[_shipment_out(db, s) for s in shipments],
        backorders=[BackorderOut.model_validate(b) for b in backorders],
    )


@router.get("/{order_id}/timeline", response_model=list[TimelineEvent],
            summary="Chronological order timeline (status changes, shipments, back-orders, cancellations)")
def timeline(order_id: int, db: Session = Depends(get_db),
             user: User = Depends(require_roles(R.CUSTOMER, *READ_STAFF))):
    order = _load_order(db, order_id, user)
    hide = user.role == R.CUSTOMER  # customers do not see staff identities
    events: list[tuple] = []

    def add(at, kind, title, detail=None, actor=None):
        events.append((_naive(at), len(events), TimelineEvent(
            at=_naive(at), type=kind, title=title, detail=detail, actor_id=None if hide else actor)))

    for h in db.scalars(select(OrderStatusHistory).where(OrderStatusHistory.order_id == order.id)).all():
        add(h.created_at, "STATUS_CHANGE",
            f"Status: {h.previous_status or 'NEW'} -> {h.new_status}", h.note, h.changed_by)
    for s in db.scalars(select(OrderShipment).where(OrderShipment.order_id == order.id)).all():
        add(s.shipped_at or s.created_at, "SHIPMENT_SHIPPED", f"Shipment {s.shipment_number} shipped",
            f"{s.carrier_name} {s.tracking_number or ''}".strip(), s.created_by)
        if s.delivered_at:
            add(s.delivered_at, "SHIPMENT_DELIVERED", f"Shipment {s.shipment_number} delivered")
    for b in db.scalars(select(OrderBackorder).where(OrderBackorder.order_id == order.id)).all():
        add(b.created_at, "BACKORDER_OPENED", f"Back-order opened for item {b.order_item_id}", b.reason, b.created_by)
        if b.resolved_at:
            add(b.resolved_at, f"BACKORDER_{b.status}", f"Back-order {b.id} {b.status.lower()}", None, b.resolved_by)
    for c in db.scalars(select(OrderItemCancellation).where(OrderItemCancellation.order_id == order.id)).all():
        add(c.created_at, "ITEM_CANCELLED", f"{c.quantity} unit(s) of item {c.order_item_id} cancelled",
            c.reason, c.cancelled_by)
    events.sort(key=lambda e: (e[0] is None, e[0], e[1]))
    return [e[2] for e in events]


# ------------------------------------------------------------------ shipments
@router.post("/{order_id}/shipments", response_model=ShipmentOut, status_code=201,
             summary="Ship part or all of an order (split shipment)",
             responses={404: {"description": "Order not found"},
                        409: {"description": "Order not shippable or quantity exceeds what is shippable"},
                        422: {"description": "Invalid body / item not in order"}},
             dependencies=[Depends(rate_limit(60))])
def create_shipment(order_id: int, payload: ShipmentCreate, idempotency_key: str | None = IDEM,
                    db: Session = Depends(get_db), user: User = Depends(require_roles(*SHIP_STAFF))):
    try:
        begun, replay = idempotency.start(db, scope=f"order.{order_id}.ship", key=idempotency_key,
                                          user_id=user.id, payload=payload.model_dump(mode="json"))
        if replay is not None:
            return replay
        order = _load_order(db, order_id, user, lock=True)
        shipment = svc.create_shipment(
            db, order, [(l.order_item_id, l.quantity) for l in payload.items],
            carrier_name=payload.carrier_name, tracking_number=payload.tracking_number,
            estimated_delivery_at=_naive(payload.estimated_delivery_at), notes=payload.notes, actor_id=user.id)
        body = _shipment_out(db, shipment)
        if begun is not None:
            idempotency.complete(db, begun, 201, jsonable_encoder(body))
        db.commit()
        return body
    except Exception:
        db.rollback()
        raise


@router.post("/{order_id}/shipments/{shipment_id}/deliver", response_model=ShipmentOut,
             summary="Mark a shipment delivered", responses={409: {"description": "Already delivered"}})
def deliver(order_id: int, shipment_id: int, db: Session = Depends(get_db),
            user: User = Depends(require_roles(*SHIP_STAFF))):
    try:
        order = _load_order(db, order_id, user, lock=True)
        shipment = db.scalar(select(OrderShipment).where(
            OrderShipment.id == shipment_id, OrderShipment.order_id == order.id).with_for_update())
        if shipment is None:
            raise HTTPException(status_code=404, detail="Shipment not found")
        svc.deliver_shipment(db, order, shipment, user.id)
        body = _shipment_out(db, shipment)
        db.commit()
        return body
    except Exception:
        db.rollback()
        raise


# ------------------------------------------------------------------ back-orders
@router.post("/{order_id}/items/{item_id}/backorder", response_model=BackorderOut, status_code=201,
             summary="Back-order units of an item that cannot be shipped yet")
def backorder(order_id: int, item_id: int, payload: BackorderCreate, db: Session = Depends(get_db),
              user: User = Depends(require_roles(*BACKORDER_STAFF))):
    try:
        order = _load_order(db, order_id, user, lock=True)
        bo = svc.create_backorder(db, order, item_id, payload.quantity, _naive(payload.expected_at),
                                  payload.reason, user.id)
        db.commit()
        db.refresh(bo)
        return bo
    except Exception:
        db.rollback()
        raise


@router.post("/{order_id}/backorders/{backorder_id}/resolve", response_model=BackorderOut,
             summary="Stock is available: make back-ordered units shippable again")
def resolve_backorder(order_id: int, backorder_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_roles(*BACKORDER_STAFF))):
    try:
        order = _load_order(db, order_id, user, lock=True)
        bo = db.scalar(select(OrderBackorder).where(
            OrderBackorder.id == backorder_id, OrderBackorder.order_id == order.id).with_for_update())
        if bo is None:
            raise HTTPException(status_code=404, detail="Back-order not found")
        svc.resolve_backorder(db, order, bo, user.id)
        db.commit()
        db.refresh(bo)
        return bo
    except Exception:
        db.rollback()
        raise


# ------------------------------------------------------------------ item cancellation
@router.post("/{order_id}/cancel-items", response_model=CancelItemsResponse,
             summary="Cancel some units of an order before they ship (stock restored, refund issued if paid)",
             responses={404: {"description": "Order not found (or not yours)"},
                        409: {"description": "Units already shipped, status not cancellable, or unpaid partial cancel"},
                        422: {"description": "Invalid body / item not in order / key reuse"}},
             dependencies=[Depends(rate_limit(30))])
def cancel_items(order_id: int, payload: CancelItemsRequest, idempotency_key: str | None = IDEM,
                 db: Session = Depends(get_db), user: User = Depends(require_roles(R.CUSTOMER, *ORDER_STAFF))):
    refund_id = None
    try:
        begun, replay = idempotency.start(db, scope=f"order.{order_id}.cancel_items", key=idempotency_key,
                                          user_id=user.id, payload=payload.model_dump(mode="json"))
        if replay is not None:
            return replay
        order = _load_order(db, order_id, user, lock=True)
        result = svc.cancel_items(db, order, [(l.order_item_id, l.quantity) for l in payload.items],
                                  payload.reason, user.id, is_staff=user.role in ORDER_STAFF)
        refund_id = result["refund_id"]
        body = CancelItemsResponse(
            order_id=order.id, order_status=result["order_status"],
            cancelled=[CancelledLine(order_item_id=r.order_item_id, quantity=r.quantity,
                                     refund_amount=r.refund_amount) for r in result["records"]],
            refund_id=refund_id, refund_status="APPROVED" if refund_id else None,
            refund_note=result["refund_note"],
        )
        if begun is not None:
            idempotency.complete(db, begun, 200, jsonable_encoder(body))
        db.commit()
    except Exception:
        db.rollback()
        raise

    # The gateway call happens after the commit (same pattern as POST /orders/{id}/cancel):
    # a gateway outage leaves the refund APPROVED for retry instead of undoing the cancellation.
    if refund_id is not None:
        try:
            body.refund_status = process_refund(db, refund_id, actor_id=user.id).status
        except RefundError as exc:
            body.refund_note = f"Refund queued but not yet processed: {exc.detail}"
        except Exception:
            db.rollback()
            body.refund_note = "Refund queued; gateway processing will be retried"
    return body


# ------------------------------------------------------------------ admin updates
@router.post("/{order_id}/status", summary="Manually move an order through PLACED -> CONFIRMED -> PROCESSING",
             responses={409: {"description": "Invalid transition or status is derived from shipments"}})
def set_status(order_id: int, payload: StatusChange, db: Session = Depends(get_db),
               user: User = Depends(require_roles(*ORDER_STAFF))):
    target = payload.status.strip().upper()
    if target not in svc.TRANSITIONS_V2:
        raise HTTPException(status_code=422, detail="Unsupported order status")
    if target == "CANCELLED":
        raise HTTPException(status_code=409, detail="Use POST /v2/orders/{id}/cancel-items (or /orders/{id}/cancel)")
    if target not in svc.MANUAL_TARGETS:
        raise HTTPException(status_code=409, detail=f"{target} is derived from shipments and cannot be set by hand")
    try:
        order = _load_order(db, order_id, user, lock=True)
        previous = svc.status_of(order)
        if previous == "BACKORDERED":
            raise HTTPException(status_code=409, detail="Resolve the open back-orders instead")
        if previous == target:
            raise HTTPException(status_code=409, detail=f"Order is already {target}")
        svc.transition(db, order, target, user.id, payload.reason)
        create_notification(db, order.user_id, f"Order {order.order_number} is {target.lower()}",
                            f"Your order moved from {previous.lower()} to {target.lower()}.",
                            notification_type="ORDER", reference_type="order", reference_id=order.id)
        db.commit()
        return {"order_id": order.id, "previous_status": previous, "status": target}
    except Exception:
        db.rollback()
        raise


@router.patch("/{order_id}", response_model=OrderAdminUpdateResponse,
              summary="Correct shipping details or notes before anything has shipped (audited)",
              responses={409: {"description": "Order already shipped/closed"}})
def admin_update(order_id: int, payload: OrderAdminUpdate, db: Session = Depends(get_db),
                 user: User = Depends(require_roles(*ORDER_STAFF))):
    changes = payload.model_dump(exclude_unset=True, exclude={"reason"})
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")
    try:
        order = _load_order(db, order_id, user, lock=True)
        if svc.status_of(order) not in svc.EDITABLE_STATUSES:
            raise HTTPException(status_code=409, detail=f"Order in status {svc.status_of(order)} can no longer be edited")
        items = svc.lock_items(db, order.id)
        if any(i.quantity_shipped for i in items):
            raise HTTPException(status_code=409, detail="Order has shipped units and can no longer be edited")
        changed = []
        for field, value in changes.items():
            if field == "shipping_country" and value is not None:
                value = value.upper()
            if value is None and field not in {"notes", "shipping_address_line2"}:
                continue
            if getattr(order, field) != value:
                setattr(order, field, value)
                changed.append(field)
        if changed:
            # Field names only: addresses are personal data and stay out of the audit text.
            write_audit_log(db, actor_user_id=user.id, action="ORDER_ADMIN_UPDATE", entity_type="order",
                            entity_id=order.id, details={"fields": changed, "reason": payload.reason}, commit=False)
            create_notification(db, order.user_id, f"Order {order.order_number} updated",
                                "Some order details were corrected by our team.",
                                notification_type="ORDER", reference_type="order", reference_id=order.id, email=True)
        db.commit()
        return OrderAdminUpdateResponse(order_id=order.id, changed_fields=changed)
    except Exception:
        db.rollback()
        raise
