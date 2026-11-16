"""Checkout stock holds.

All functions ``add``/``flush`` only; the caller commits so a hold change is
atomic with the business change that caused it. Stock rows are locked
(``SELECT ... FOR UPDATE``) in (product, variant) order to avoid deadlocks.
"""
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import utcnow
from app.models.cart import Cart, CartItem
from app.models.inventory import Inventory
from app.models.inventory_reservation import InventoryReservation
from app.models.variant_inventory import VariantInventory


def now_naive():
    """Naive UTC, matching how DateTime columns come back from MySQL."""
    return utcnow().replace(tzinfo=None)


def _lock_stock(db: Session, product_id: int, variant_id: int | None):
    if variant_id is not None:
        stmt = select(VariantInventory).where(VariantInventory.variant_id == variant_id)
    else:
        stmt = select(Inventory).where(Inventory.product_id == product_id)
    return db.scalar(stmt.with_for_update())


def _give_back(db: Session, res: InventoryReservation, status: str, reason: str) -> None:
    stock = _lock_stock(db, res.product_id, res.variant_id)
    if stock is not None:
        stock.reserved_quantity = max(0, stock.reserved_quantity - res.quantity)
    res.status = status
    res.release_reason = reason[:255]
    res.released_at = now_naive()


def active_holds(db: Session, user_id: int) -> list[InventoryReservation]:
    return list(
        db.scalars(
            select(InventoryReservation)
            .where(InventoryReservation.user_id == user_id, InventoryReservation.status == "ACTIVE")
            .order_by(InventoryReservation.product_id, InventoryReservation.variant_id, InventoryReservation.id)
            .with_for_update()
        ).all()
    )


def release_reservation(db: Session, res: InventoryReservation, reason: str, status: str = "RELEASED") -> bool:
    """Release one hold. Returns False if it was no longer ACTIVE (idempotent)."""
    if res.status != "ACTIVE":
        return False
    _give_back(db, res, status, reason)
    return True


def release_user_holds(db: Session, user_id: int, reason: str, status: str = "RELEASED") -> int:
    count = 0
    for res in active_holds(db, user_id):
        _give_back(db, res, status, reason)
        count += 1
    db.flush()
    return count


def convert_user_holds(db: Session, user_id: int) -> list[InventoryReservation]:
    """Called by checkout: the order deducts stock itself, so the user's holds
    are released first. Holds already past expiry are marked EXPIRED."""
    now = now_naive()
    converted = []
    for res in active_holds(db, user_id):
        if res.expires_at > now:
            _give_back(db, res, "CONVERTED", "Converted to order")
            converted.append(res)
        else:
            _give_back(db, res, "EXPIRED", "Hold expired before checkout")
    db.flush()
    return converted


def reserve_cart(db: Session, user_id: int, ttl_minutes: int | None = None) -> list[InventoryReservation]:
    ttl = ttl_minutes or settings.reservation_ttl_minutes
    ttl = min(ttl, settings.reservation_max_ttl_minutes)

    cart = db.scalar(select(Cart).where(Cart.user_id == user_id).with_for_update())
    items = []
    if cart is not None:
        items = list(
            db.scalars(
                select(CartItem)
                .where(CartItem.cart_id == cart.id)
                .order_by(CartItem.product_id, CartItem.variant_id, CartItem.id)
            ).all()
        )
    if not items:
        raise HTTPException(status_code=409, detail="Cart is empty")

    # Re-reserving replaces the user's previous holds instead of stacking them.
    release_user_holds(db, user_id, "Replaced by a new reservation")

    group = uuid4().hex
    expires = now_naive() + timedelta(minutes=ttl)
    created = []
    for item in items:
        stock = _lock_stock(db, item.product_id, item.variant_id)
        if stock is None or stock.available_quantity < item.quantity:
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock to reserve product {item.product_id}",
            )
        stock.reserved_quantity += item.quantity
        res = InventoryReservation(
            group_id=group,
            user_id=user_id,
            product_id=item.product_id,
            variant_id=item.variant_id,
            quantity=item.quantity,
            status="ACTIVE",
            expires_at=expires,
        )
        db.add(res)
        created.append(res)
    db.flush()
    return created


def expire_due_reservations(db: Session, limit: int = 500) -> int:
    """Release every ACTIVE hold past its expiry. Safe to run repeatedly."""
    due = list(
        db.scalars(
            select(InventoryReservation)
            .where(InventoryReservation.status == "ACTIVE", InventoryReservation.expires_at <= now_naive())
            .order_by(InventoryReservation.product_id, InventoryReservation.variant_id, InventoryReservation.id)
            .limit(limit)
            .with_for_update()
        ).all()
    )
    for res in due:
        release_reservation(db, res, "Reservation expired", status="EXPIRED")
    db.commit()
    return len(due)
