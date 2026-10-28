
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.db.session import get_db
from app.models.product_variant import ProductVariant
from app.models.user import User, UserRole
from app.models.variant_inventory import (
    VariantInventory,
    VariantInventoryMovement,
)
from app.schemas.variant_inventory import (
    VariantInventoryAdjustment,
    VariantInventoryCreate,
    VariantInventoryMovementResponse,
    VariantInventoryResponse,
)


router = APIRouter(
    prefix="/variant-inventory",
    tags=["Variant Inventory"],
)

INVENTORY_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.INVENTORY_MANAGER,
)


def get_active_variant(
    db: Session,
    variant_id: int,
) -> ProductVariant:
    variant = db.get(ProductVariant, variant_id)

    if variant is None or not variant.is_active:
        raise HTTPException(
            status_code=404,
            detail="Active product variant not found",
        )

    return variant


def get_inventory_for_update(
    db: Session,
    variant_id: int,
) -> VariantInventory:
    inventory = db.scalar(
        select(VariantInventory)
        .where(VariantInventory.variant_id == variant_id)
        .with_for_update()
    )

    if inventory is None:
        raise HTTPException(
            status_code=404,
            detail="Inventory record for this variant not found",
        )

    return inventory


@router.post(
    "",
    response_model=VariantInventoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_variant_inventory(
    payload: VariantInventoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_ROLES)),
):
    get_active_variant(db, payload.variant_id)

    inventory = VariantInventory(
        variant_id=payload.variant_id,
        quantity_on_hand=payload.quantity_on_hand,
        reserved_quantity=0,
        low_stock_threshold=payload.low_stock_threshold,
    )
    db.add(inventory)

    try:
        db.flush()

        if payload.quantity_on_hand > 0:
            db.add(
                VariantInventoryMovement(
                    variant_inventory_id=inventory.id,
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
            status_code=409,
            detail="Inventory already exists for this variant",
        ) from None

    return inventory


@router.get("", response_model=list[VariantInventoryResponse])
def list_variant_inventory(
    low_stock_only: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_ROLES)),
):
    statement = select(VariantInventory)

    if low_stock_only:
        statement = statement.where(
            (
                VariantInventory.quantity_on_hand
                - VariantInventory.reserved_quantity
            )
            <= VariantInventory.low_stock_threshold
        )

    statement = (
        statement.order_by(VariantInventory.id)
        .offset(offset)
        .limit(limit)
    )

    return list(db.scalars(statement).all())


@router.get(
    "/{variant_id}",
    response_model=VariantInventoryResponse,
)
def get_variant_inventory(
    variant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_ROLES)),
):
    get_active_variant(db, variant_id)

    inventory = db.scalar(
        select(VariantInventory).where(
            VariantInventory.variant_id == variant_id
        )
    )

    if inventory is None:
        raise HTTPException(
            status_code=404,
            detail="Inventory record for this variant not found",
        )

    return inventory


@router.post(
    "/{variant_id}/adjust",
    response_model=VariantInventoryResponse,
)
def adjust_variant_inventory(
    variant_id: int,
    payload: VariantInventoryAdjustment,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_ROLES)),
):
    get_active_variant(db, variant_id)
    inventory = get_inventory_for_update(db, variant_id)

    new_quantity = (
        inventory.quantity_on_hand + payload.quantity_change
    )

    if new_quantity < inventory.reserved_quantity:
        raise HTTPException(
            status_code=409,
            detail=(
                "Adjustment would reduce stock below the reserved quantity"
            ),
        )

    if new_quantity < 0:
        raise HTTPException(
            status_code=409,
            detail="Stock cannot become negative",
        )

    inventory.quantity_on_hand = new_quantity

    db.add(
        VariantInventoryMovement(
            variant_inventory_id=inventory.id,
            quantity_change=payload.quantity_change,
            reason=payload.reason,
            performed_by=current_user.id,
        )
    )

    try:
        db.commit()
        db.refresh(inventory)
    except Exception:
        db.rollback()
        raise

    return inventory


@router.get(
    "/{variant_id}/movements",
    response_model=list[VariantInventoryMovementResponse],
)
def list_variant_inventory_movements(
    variant_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_ROLES)),
):
    get_active_variant(db, variant_id)

    inventory = db.scalar(
        select(VariantInventory).where(
            VariantInventory.variant_id == variant_id
        )
    )

    if inventory is None:
        raise HTTPException(
            status_code=404,
            detail="Inventory record for this variant not found",
        )

    statement = (
        select(VariantInventoryMovement)
        .where(
            VariantInventoryMovement.variant_inventory_id == inventory.id
        )
        .order_by(
            VariantInventoryMovement.created_at.desc(),
            VariantInventoryMovement.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )

    return list(db.scalars(statement).all())
