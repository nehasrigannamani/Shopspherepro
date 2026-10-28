
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_roles
from app.db.session import get_db
from app.models.product import Product
from app.models.user import User, UserRole
from app.models.warehouse import (
    Warehouse,
    WarehouseStock,
    WarehouseStockMovement,
)
from app.models.inventory_transfer import InventoryTransfer
from app.schemas.inventory_transfer import (
    InventoryTransferCreate,
    InventoryTransferResponse,
    InventoryTransferListResponse,
)

router = APIRouter(
    prefix="/inventory-transfers",
    tags=["Inventory Transfers"],
)

TRANSFER_STAFF = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.INVENTORY_MANAGER,
)


def now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_transfer_or_404(
    db: Session, transfer_id: int, lock: bool = False
) -> InventoryTransfer:
    statement = select(InventoryTransfer).where(
        InventoryTransfer.id == transfer_id
    )
    if lock:
        statement = statement.with_for_update()

    transfer = db.scalar(statement)
    if transfer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventory transfer not found.",
        )
    return transfer


def get_warehouse_or_404(
    db: Session, warehouse_id: int, lock: bool = False
) -> Warehouse:
    statement = select(Warehouse).where(Warehouse.id == warehouse_id)
    if lock:
        statement = statement.with_for_update()

    warehouse = db.scalar(statement)
    if warehouse is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Warehouse {warehouse_id} was not found.",
        )
    return warehouse


def get_stock(
    db: Session,
    warehouse_id: int,
    product_id: int,
    lock: bool = False,
) -> WarehouseStock | None:
    statement = select(WarehouseStock).where(
        WarehouseStock.warehouse_id == warehouse_id,
        WarehouseStock.product_id == product_id,
    )
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def check_destination_capacity(
    db: Session, warehouse: Warehouse, incoming_quantity: int
) -> None:
    if warehouse.capacity_units is None:
        return

    current_quantity = db.scalar(
        select(
            func.coalesce(func.sum(WarehouseStock.quantity_on_hand), 0)
        ).where(WarehouseStock.warehouse_id == warehouse.id)
    ) or 0

    if current_quantity + incoming_quantity > warehouse.capacity_units:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Receiving this transfer would exceed destination warehouse capacity.",
        )


@router.post(
    "",
    response_model=InventoryTransferResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_transfer(
    payload: InventoryTransferCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*TRANSFER_STAFF)),
):
    source = get_warehouse_or_404(db, payload.source_warehouse_id)
    destination = get_warehouse_or_404(
        db, payload.destination_warehouse_id
    )

    if not source.is_active or source.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The source warehouse is not active.",
        )

    if not destination.is_active or destination.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The destination warehouse is not active.",
        )

    product = db.get(Product, payload.product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found.",
        )

    if not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inactive products cannot be transferred.",
        )

    source_stock = get_stock(
        db, payload.source_warehouse_id, payload.product_id
    )
    if source_stock is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The source warehouse has no stock record for this product.",
        )

    if source_stock.quantity_on_hand < payload.quantity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Insufficient source stock for the requested transfer.",
        )

    transfer = InventoryTransfer(
        **payload.model_dump(),
        status="PENDING",
        created_by=current_user.id,
    )
    db.add(transfer)
    db.commit()
    db.refresh(transfer)
    return transfer


@router.get("", response_model=InventoryTransferListResponse)
def list_transfers(
    status_filter: str | None = Query(
        default=None,
        alias="status",
        pattern="^(PENDING|DISPATCHED|RECEIVED|CANCELLED)$",
    ),
    warehouse_id: int | None = Query(default=None, gt=0),
    product_id: int | None = Query(default=None, gt=0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*TRANSFER_STAFF)),
):
    statement = select(InventoryTransfer)

    if status_filter:
        statement = statement.where(
            InventoryTransfer.status == status_filter
        )

    if warehouse_id is not None:
        statement = statement.where(
            (InventoryTransfer.source_warehouse_id == warehouse_id)
            | (
                InventoryTransfer.destination_warehouse_id
                == warehouse_id
            )
        )

    if product_id is not None:
        statement = statement.where(
            InventoryTransfer.product_id == product_id
        )

    total = db.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0

    transfers = list(
        db.scalars(
            statement.order_by(InventoryTransfer.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )

    return InventoryTransferListResponse(
        items=transfers,
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/{transfer_id}", response_model=InventoryTransferResponse)
def get_transfer(
    transfer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*TRANSFER_STAFF)),
):
    return get_transfer_or_404(db, transfer_id)


@router.post(
    "/{transfer_id}/dispatch",
    response_model=InventoryTransferResponse,
)
def dispatch_transfer(
    transfer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*TRANSFER_STAFF)),
):
    transfer = get_transfer_or_404(db, transfer_id, lock=True)

    if transfer.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only pending transfers can be dispatched.",
        )

    source = get_warehouse_or_404(
        db, transfer.source_warehouse_id, lock=True
    )
    if not source.is_active or source.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The source warehouse is not active.",
        )

    source_stock = get_stock(
        db,
        transfer.source_warehouse_id,
        transfer.product_id,
        lock=True,
    )

    if source_stock is None or source_stock.quantity_on_hand < transfer.quantity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Insufficient source stock to dispatch this transfer.",
        )

    source_stock.quantity_on_hand -= transfer.quantity

    db.add(
        WarehouseStockMovement(
            warehouse_stock_id=source_stock.id,
            quantity_change=-transfer.quantity,
            reason=f"Transfer #{transfer.id} dispatched: {transfer.reason}",
            performed_by=current_user.id,
        )
    )

    transfer.status = "DISPATCHED"
    transfer.dispatched_by = current_user.id
    transfer.dispatched_at = now_utc_naive()

    db.commit()
    db.refresh(transfer)
    return transfer


@router.post(
    "/{transfer_id}/receive",
    response_model=InventoryTransferResponse,
)
def receive_transfer(
    transfer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*TRANSFER_STAFF)),
):
    transfer = get_transfer_or_404(db, transfer_id, lock=True)

    if transfer.status != "DISPATCHED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only dispatched transfers can be received.",
        )

    destination = get_warehouse_or_404(
        db, transfer.destination_warehouse_id, lock=True
    )

    if not destination.is_active or destination.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The destination warehouse is not active.",
        )

    check_destination_capacity(db, destination, transfer.quantity)

    destination_stock = get_stock(
        db,
        transfer.destination_warehouse_id,
        transfer.product_id,
        lock=True,
    )

    if destination_stock is None:
        destination_stock = WarehouseStock(
            warehouse_id=transfer.destination_warehouse_id,
            product_id=transfer.product_id,
            quantity_on_hand=transfer.quantity,
        )
        db.add(destination_stock)
        db.flush()

        movement_quantity = transfer.quantity
    else:
        destination_stock.quantity_on_hand += transfer.quantity
        movement_quantity = transfer.quantity

    db.add(
        WarehouseStockMovement(
            warehouse_stock_id=destination_stock.id,
            quantity_change=movement_quantity,
            reason=f"Transfer #{transfer.id} received: {transfer.reason}",
            performed_by=current_user.id,
        )
    )

    transfer.status = "RECEIVED"
    transfer.received_by = current_user.id
    transfer.received_at = now_utc_naive()

    db.commit()
    db.refresh(transfer)
    return transfer


@router.post(
    "/{transfer_id}/cancel",
    response_model=InventoryTransferResponse,
)
def cancel_transfer(
    transfer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*TRANSFER_STAFF)),
):
    transfer = get_transfer_or_404(db, transfer_id, lock=True)

    if transfer.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only pending transfers can be cancelled. Dispatched transfers require a separate return transfer.",
        )

    transfer.status = "CANCELLED"
    db.commit()
    db.refresh(transfer)
    return transfer
