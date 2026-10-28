
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
from app.schemas.warehouse import (
    WarehouseCreate,
    WarehouseUpdate,
    WarehouseResponse,
    WarehouseStockCreate,
    WarehouseStockAdjustment,
    WarehouseStockResponse,
    WarehouseStockMovementResponse,
)

router = APIRouter(prefix="/warehouses", tags=["Warehouse Management"])

WAREHOUSE_STAFF = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.INVENTORY_MANAGER,
)


def get_warehouse_or_404(db: Session, warehouse_id: int) -> Warehouse:
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Warehouse not found.",
        )
    return warehouse


def get_stock_or_404(
    db: Session, warehouse_id: int, product_id: int
) -> WarehouseStock:
    stock = db.scalar(
        select(WarehouseStock)
        .where(
            WarehouseStock.warehouse_id == warehouse_id,
            WarehouseStock.product_id == product_id,
        )
        .with_for_update()
    )
    if stock is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stock record not found for this warehouse and product.",
        )
    return stock


def check_capacity(
    db: Session, warehouse: Warehouse, proposed_quantity: int
) -> None:
    if warehouse.capacity_units is None:
        return

    current_total = db.scalar(
        select(func.coalesce(func.sum(WarehouseStock.quantity_on_hand), 0))
        .where(WarehouseStock.warehouse_id == warehouse.id)
    ) or 0

    if current_total + proposed_quantity > warehouse.capacity_units:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This stock change would exceed the warehouse capacity.",
        )


@router.post(
    "",
    response_model=WarehouseResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_warehouse(
    payload: WarehouseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WAREHOUSE_STAFF)),
):
    warehouse = Warehouse(**payload.model_dump())
    db.add(warehouse)

    try:
        db.commit()
        db.refresh(warehouse)
        return warehouse
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A warehouse with this code already exists.",
        ) from exc


@router.get("", response_model=list[WarehouseResponse])
def list_warehouses(
    status_filter: str | None = Query(
        default=None, alias="status",
        pattern="^(ACTIVE|INACTIVE|MAINTENANCE)$",
    ),
    include_inactive: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    statement = select(Warehouse)

    if not include_inactive:
        statement = statement.where(Warehouse.is_active.is_(True))

    if status_filter:
        statement = statement.where(Warehouse.status == status_filter)

    return list(
        db.scalars(
            statement.order_by(Warehouse.name)
            .offset(offset)
            .limit(limit)
        ).all()
    )


@router.get("/{warehouse_id}", response_model=WarehouseResponse)
def get_warehouse(
    warehouse_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_warehouse_or_404(db, warehouse_id)


@router.patch("/{warehouse_id}", response_model=WarehouseResponse)
def update_warehouse(
    warehouse_id: int,
    payload: WarehouseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WAREHOUSE_STAFF)),
):
    warehouse = get_warehouse_or_404(db, warehouse_id)
    changes = payload.model_dump(exclude_unset=True)

    new_capacity = changes.get("capacity_units", warehouse.capacity_units)
    if new_capacity is not None:
        current_total = db.scalar(
            select(func.coalesce(func.sum(WarehouseStock.quantity_on_hand), 0))
            .where(WarehouseStock.warehouse_id == warehouse.id)
        ) or 0

        if new_capacity < current_total:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Capacity cannot be set below the warehouse's "
                    "current stock quantity."
                ),
            )

    new_status = changes.get("status", warehouse.status)
    new_active = changes.get("is_active", warehouse.is_active)

    if new_status == "ACTIVE" and not new_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="An inactive warehouse cannot have ACTIVE status.",
        )

    for field, value in changes.items():
        setattr(warehouse, field, value)

    try:
        db.commit()
        db.refresh(warehouse)
        return warehouse
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The warehouse code is already in use.",
        ) from exc


@router.delete("/{warehouse_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_warehouse(
    warehouse_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WAREHOUSE_STAFF)),
):
    warehouse = get_warehouse_or_404(db, warehouse_id)
    warehouse.is_active = False
    warehouse.status = "INACTIVE"
    db.commit()
    return None


@router.post(
    "/{warehouse_id}/stock",
    response_model=WarehouseStockResponse,
    status_code=status.HTTP_201_CREATED,
)
def initialize_warehouse_stock(
    warehouse_id: int,
    payload: WarehouseStockCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WAREHOUSE_STAFF)),
):
    warehouse = get_warehouse_or_404(db, warehouse_id)

    if not warehouse.is_active or warehouse.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stock cannot be added to an inactive warehouse.",
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
            detail="Stock cannot be initialized for an inactive product.",
        )

    existing = db.scalar(
        select(WarehouseStock).where(
            WarehouseStock.warehouse_id == warehouse_id,
            WarehouseStock.product_id == payload.product_id,
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stock already exists for this product in this warehouse. Use the adjustment endpoint.",
        )

    check_capacity(db, warehouse, payload.quantity_on_hand)

    stock = WarehouseStock(
        warehouse_id=warehouse_id,
        product_id=payload.product_id,
        quantity_on_hand=payload.quantity_on_hand,
    )
    db.add(stock)
    db.flush()

    db.add(
        WarehouseStockMovement(
            warehouse_stock_id=stock.id,
            quantity_change=payload.quantity_on_hand,
            reason=payload.reason.strip(),
            performed_by=current_user.id,
        )
    )

    try:
        db.commit()
        db.refresh(stock)
        return stock
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stock already exists for this product in this warehouse.",
        ) from exc


@router.get(
    "/{warehouse_id}/stock",
    response_model=list[WarehouseStockResponse],
)
def list_warehouse_stock(
    warehouse_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_warehouse_or_404(db, warehouse_id)

    return list(
        db.scalars(
            select(WarehouseStock)
            .where(WarehouseStock.warehouse_id == warehouse_id)
            .order_by(WarehouseStock.product_id)
            .offset(offset)
            .limit(limit)
        ).all()
    )


@router.post(
    "/{warehouse_id}/stock/{product_id}/adjust",
    response_model=WarehouseStockResponse,
)
def adjust_warehouse_stock(
    warehouse_id: int,
    product_id: int,
    payload: WarehouseStockAdjustment,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WAREHOUSE_STAFF)),
):
    warehouse = get_warehouse_or_404(db, warehouse_id)

    if not warehouse.is_active or warehouse.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stock cannot be adjusted in an inactive warehouse.",
        )

    stock = get_stock_or_404(db, warehouse_id, product_id)
    new_quantity = stock.quantity_on_hand + payload.quantity_change

    if new_quantity < 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stock quantity cannot become negative.",
        )

    check_capacity(db, warehouse, payload.quantity_change)

    stock.quantity_on_hand = new_quantity
    db.add(
        WarehouseStockMovement(
            warehouse_stock_id=stock.id,
            quantity_change=payload.quantity_change,
            reason=payload.reason.strip(),
            performed_by=current_user.id,
        )
    )

    db.commit()
    db.refresh(stock)
    return stock


@router.get(
    "/{warehouse_id}/stock/{product_id}/movements",
    response_model=list[WarehouseStockMovementResponse],
)
def get_warehouse_stock_movements(
    warehouse_id: int,
    product_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stock = get_stock_or_404(db, warehouse_id, product_id)

    return list(
        db.scalars(
            select(WarehouseStockMovement)
            .where(
                WarehouseStockMovement.warehouse_stock_id == stock.id
            )
            .order_by(WarehouseStockMovement.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
