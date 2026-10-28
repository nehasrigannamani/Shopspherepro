
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.db.session import get_db
from app.models.inventory import Inventory, InventoryMovement
from app.models.product import Product
from app.models.user import User, UserRole
from app.schemas.inventory import (
    InventoryAdjustment,
    InventoryCreate,
    InventoryMovementResponse,
    InventoryResponse,
)


router = APIRouter(prefix="/inventory", tags=["Inventory"])

INVENTORY_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.INVENTORY_MANAGER,
)


@router.post(
    "",
    response_model=InventoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_inventory(
    payload: InventoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_ROLES)),
):
    product = db.scalar(
        select(Product).where(
            Product.id == payload.product_id,
            Product.is_active.is_(True),
        )
    )

    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active product not found",
        )

    existing = db.scalar(
        select(Inventory).where(
            Inventory.product_id == payload.product_id
        )
    )

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inventory already exists for this product",
        )

    inventory = Inventory(
        product_id=payload.product_id,
        quantity_on_hand=payload.quantity_on_hand,
        reserved_quantity=0,
        low_stock_threshold=payload.low_stock_threshold,
    )

    try:
        db.add(inventory)
        db.flush()

        db.add(
            InventoryMovement(
                inventory_id=inventory.id,
                quantity_change=payload.quantity_on_hand,
                reason="Initial stock",
                performed_by=current_user.id,
            )
        )

        db.commit()
        db.refresh(inventory)

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inventory could not be created; check whether it already exists",
        ) from None

    return inventory


@router.get("", response_model=list[InventoryResponse])
def list_inventory(
    low_stock_only: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_ROLES)),
):
    statement = select(Inventory)

    if low_stock_only:
        statement = statement.where(
            Inventory.quantity_on_hand - Inventory.reserved_quantity
            <= Inventory.low_stock_threshold
        )

    statement = statement.order_by(Inventory.id).offset(offset).limit(limit)
    return list(db.scalars(statement).all())


@router.get(
    "/{product_id}/movements",
    response_model=list[InventoryMovementResponse],
)
def list_inventory_movements(
    product_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_ROLES)),
):
    inventory = db.scalar(
        select(Inventory).where(Inventory.product_id == product_id)
    )

    if inventory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventory not found for this product",
        )

    statement = (
        select(InventoryMovement)
        .where(InventoryMovement.inventory_id == inventory.id)
        .order_by(InventoryMovement.id.desc())
        .offset(offset)
        .limit(limit)
    )

    return list(db.scalars(statement).all())


@router.post(
    "/{product_id}/adjust",
    response_model=InventoryResponse,
)
def adjust_inventory(
    product_id: int,
    payload: InventoryAdjustment,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_ROLES)),
):
    inventory = db.scalar(
        select(Inventory)
        .where(Inventory.product_id == product_id)
        .with_for_update()
    )

    if inventory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventory not found for this product",
        )

    new_quantity = inventory.quantity_on_hand + payload.quantity_change

    if new_quantity < inventory.reserved_quantity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Stock cannot be reduced below the currently reserved quantity"
            ),
        )

    if new_quantity < 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Insufficient stock for this adjustment",
        )

    inventory.quantity_on_hand = new_quantity

    db.add(
        InventoryMovement(
            inventory_id=inventory.id,
            quantity_change=payload.quantity_change,
            reason=payload.reason.strip(),
            performed_by=current_user.id,
        )
    )

    db.commit()
    db.refresh(inventory)

    return inventory
