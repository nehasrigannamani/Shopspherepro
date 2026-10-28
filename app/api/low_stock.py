
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.db.session import get_db
from app.models.inventory import Inventory
from app.models.low_stock import (
    LowStockThresholdHistory,
    WarehouseStockThreshold,
)
from app.models.product import Product
from app.models.user import User, UserRole
from app.models.warehouse import Warehouse, WarehouseStock
from app.schemas.low_stock import (
    GlobalLowStockItem,
    LowStockThresholdHistoryResponse,
    ThresholdHistoryPage,
    ThresholdUpdate,
    WarehouseLowStockItem,
)

router = APIRouter(prefix="/low-stock", tags=["Low-Stock Management"])

INVENTORY_STAFF = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.INVENTORY_MANAGER,
)


def add_threshold_history(
    db: Session,
    current_user: User,
    *,
    new_threshold: int,
    old_threshold: int | None,
    inventory_id: int | None = None,
    warehouse_stock_id: int | None = None,
) -> None:
    db.add(
        LowStockThresholdHistory(
            inventory_id=inventory_id,
            warehouse_stock_id=warehouse_stock_id,
            old_threshold=old_threshold,
            new_threshold=new_threshold,
            changed_by=current_user.id,
        )
    )


@router.get("/inventory", response_model=list[GlobalLowStockItem])
def get_global_low_stock(
    product_id: int | None = Query(default=None, gt=0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_STAFF)),
):
    statement = (
        select(Inventory, Product)
        .join(Product, Product.id == Inventory.product_id)
        .where(
            Inventory.quantity_on_hand - Inventory.reserved_quantity
            <= Inventory.low_stock_threshold
        )
    )

    if product_id is not None:
        statement = statement.where(Inventory.product_id == product_id)

    rows = db.execute(
        statement.order_by(
            (Inventory.quantity_on_hand - Inventory.reserved_quantity).asc()
        )
        .offset(offset)
        .limit(limit)
    ).all()

    return [
        {
            "inventory_id": inventory.id,
            "product_id": product.id,
            "product_name": product.name,
            "sku": product.sku,
            "quantity_on_hand": inventory.quantity_on_hand,
            "reserved_quantity": inventory.reserved_quantity,
            "available_quantity": (
                inventory.quantity_on_hand - inventory.reserved_quantity
            ),
            "threshold": inventory.low_stock_threshold,
        }
        for inventory, product in rows
    ]


@router.get("/warehouses", response_model=list[WarehouseLowStockItem])
def get_warehouse_low_stock(
    warehouse_id: int | None = Query(default=None, gt=0),
    product_id: int | None = Query(default=None, gt=0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_STAFF)),
):
    threshold_expr = func.coalesce(
        WarehouseStockThreshold.threshold, 5
    )

    statement = (
        select(WarehouseStock, Warehouse, Product, threshold_expr)
        .join(Warehouse, Warehouse.id == WarehouseStock.warehouse_id)
        .join(Product, Product.id == WarehouseStock.product_id)
        .outerjoin(
            WarehouseStockThreshold,
            WarehouseStockThreshold.warehouse_stock_id
            == WarehouseStock.id,
        )
        .where(WarehouseStock.quantity_on_hand <= threshold_expr)
    )

    if warehouse_id is not None:
        statement = statement.where(
            WarehouseStock.warehouse_id == warehouse_id
        )

    if product_id is not None:
        statement = statement.where(
            WarehouseStock.product_id == product_id
        )

    rows = db.execute(
        statement.order_by(WarehouseStock.quantity_on_hand.asc())
        .offset(offset)
        .limit(limit)
    ).all()

    return [
        {
            "warehouse_stock_id": stock.id,
            "warehouse_id": warehouse.id,
            "warehouse_name": warehouse.name,
            "product_id": product.id,
            "product_name": product.name,
            "sku": product.sku,
            "quantity_on_hand": stock.quantity_on_hand,
            "threshold": threshold,
        }
        for stock, warehouse, product, threshold in rows
    ]


@router.put("/inventory/{inventory_id}/threshold")
def update_global_threshold(
    inventory_id: int,
    payload: ThresholdUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_STAFF)),
):
    inventory = db.scalar(
        select(Inventory)
        .where(Inventory.id == inventory_id)
        .with_for_update()
    )

    if inventory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventory record not found.",
        )

    old_threshold = inventory.low_stock_threshold
    inventory.low_stock_threshold = payload.threshold

    add_threshold_history(
        db,
        current_user,
        inventory_id=inventory.id,
        old_threshold=old_threshold,
        new_threshold=payload.threshold,
    )

    db.commit()

    return {
        "message": "Inventory low-stock threshold updated.",
        "inventory_id": inventory.id,
        "old_threshold": old_threshold,
        "new_threshold": inventory.low_stock_threshold,
    }


@router.put("/warehouses/stock/{warehouse_stock_id}/threshold")
def update_warehouse_threshold(
    warehouse_stock_id: int,
    payload: ThresholdUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_STAFF)),
):
    stock = db.scalar(
        select(WarehouseStock)
        .where(WarehouseStock.id == warehouse_stock_id)
        .with_for_update()
    )

    if stock is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Warehouse stock record not found.",
        )

    threshold_record = db.scalar(
        select(WarehouseStockThreshold)
        .where(
            WarehouseStockThreshold.warehouse_stock_id == stock.id
        )
        .with_for_update()
    )

    old_threshold = (
        threshold_record.threshold if threshold_record else 5
    )

    if threshold_record is None:
        threshold_record = WarehouseStockThreshold(
            warehouse_stock_id=stock.id,
            threshold=payload.threshold,
        )
        db.add(threshold_record)
    else:
        threshold_record.threshold = payload.threshold

    add_threshold_history(
        db,
        current_user,
        warehouse_stock_id=stock.id,
        old_threshold=old_threshold,
        new_threshold=payload.threshold,
    )

    db.commit()

    return {
        "message": "Warehouse stock threshold updated.",
        "warehouse_stock_id": stock.id,
        "old_threshold": old_threshold,
        "new_threshold": payload.threshold,
    }


@router.get("/threshold-history", response_model=ThresholdHistoryPage)
def get_threshold_history(
    inventory_id: int | None = Query(default=None, gt=0),
    warehouse_stock_id: int | None = Query(default=None, gt=0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_STAFF)),
):
    if inventory_id is not None and warehouse_stock_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Filter by inventory_id or warehouse_stock_id, not both.",
        )

    statement = select(LowStockThresholdHistory)

    if inventory_id is not None:
        statement = statement.where(
            LowStockThresholdHistory.inventory_id == inventory_id
        )

    if warehouse_stock_id is not None:
        statement = statement.where(
            LowStockThresholdHistory.warehouse_stock_id
            == warehouse_stock_id
        )

    total = db.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0

    items = list(
        db.scalars(
            statement.order_by(
                LowStockThresholdHistory.created_at.desc()
            )
            .offset(offset)
            .limit(limit)
        ).all()
    )

    return ThresholdHistoryPage(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )
