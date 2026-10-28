
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_roles
from app.db.session import get_db
from app.models.supplier import Supplier
from app.models.user import User
from app.schemas.suppliers import (
    SupplierCreate,
    SupplierResponse,
    SupplierStatusUpdate,
    SupplierUpdate,
)


router = APIRouter(
    prefix="/suppliers",
    tags=["Seller and Supplier Management"],
)


SUPPLIER_ROLES = (
    "SUPER_ADMIN",
    "ADMIN",
    "INVENTORY_MANAGER",
    "FINANCE_MANAGER",
)


@router.post(
    "",
    response_model=SupplierResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_supplier(
    payload: SupplierCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*SUPPLIER_ROLES)),
):
    supplier = Supplier(
        **payload.model_dump(),
        email=str(payload.email).lower(),
    )

    db.add(supplier)

    try:
        db.commit()
        db.refresh(supplier)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A supplier with this email or tax identifier already exists",
        )

    return supplier


@router.get(
    "",
    response_model=list[SupplierResponse],
)
def list_suppliers(
    search: str | None = Query(default=None, max_length=200),
    active_only: bool = True,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*SUPPLIER_ROLES)),
):
    query = db.query(Supplier)

    if active_only:
        query = query.filter(Supplier.is_active.is_(True))

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            Supplier.name.ilike(term)
            | Supplier.email.ilike(term)
            | Supplier.city.ilike(term)
        )

    return (
        query.order_by(Supplier.name.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get(
    "/{supplier_id}",
    response_model=SupplierResponse,
)
def get_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*SUPPLIER_ROLES)),
):
    supplier = (
        db.query(Supplier)
        .filter(Supplier.id == supplier_id)
        .first()
    )

    if supplier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found",
        )

    return supplier


@router.patch(
    "/{supplier_id}",
    response_model=SupplierResponse,
)
def update_supplier(
    supplier_id: int,
    payload: SupplierUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*SUPPLIER_ROLES)),
):
    supplier = (
        db.query(Supplier)
        .filter(Supplier.id == supplier_id)
        .first()
    )

    if supplier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found",
        )

    updates = payload.model_dump(exclude_unset=True)

    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one field to update",
        )

    if "email" in updates and updates["email"] is not None:
        updates["email"] = str(updates["email"]).lower()

    for field, value in updates.items():
        setattr(supplier, field, value)

    try:
        db.commit()
        db.refresh(supplier)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A supplier with this email or tax identifier already exists",
        )

    return supplier


@router.patch(
    "/{supplier_id}/status",
    response_model=SupplierResponse,
)
def update_supplier_status(
    supplier_id: int,
    payload: SupplierStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*SUPPLIER_ROLES)),
):
    supplier = (
        db.query(Supplier)
        .filter(Supplier.id == supplier_id)
        .first()
    )

    if supplier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found",
        )

    supplier.is_active = payload.is_active

    db.commit()
    db.refresh(supplier)

    return supplier
