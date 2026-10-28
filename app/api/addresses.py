from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.account import Address
from app.models.user import User, UserRole
from app.schemas.users import (
    AddressCreate,
    AddressResponse,
    AddressUpdate,
)

router = APIRouter(prefix="/addresses", tags=["Address Management"])

STAFF_VIEWERS = {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.SUPPORT_AGENT}


def _get_owned(db: Session, address_id: int, user: User) -> Address:
    address = db.get(Address, address_id)
    # Same 404 for "missing" and "someone else's": no existence leak.
    if address is None or address.user_id != user.id:
        raise HTTPException(status_code=404, detail="Address not found")
    return address


def _clear_default(db: Session, user_id: int, kind: str, keep_id: int | None) -> None:
    column = Address.is_default_shipping if kind == "SHIPPING" else Address.is_default_billing
    query = db.query(Address).filter(Address.user_id == user_id, column.is_(True))
    if keep_id is not None:
        query = query.filter(Address.id != keep_id)
    for other in query.all():
        if kind == "SHIPPING":
            other.is_default_shipping = False
        else:
            other.is_default_billing = False


def _applies(address: Address, kind: str) -> bool:
    return address.address_type in (kind, "BOTH")


def _make_default(db: Session, address: Address, kind: str) -> None:
    _clear_default(db, address.user_id, kind, address.id)
    if kind == "SHIPPING":
        address.is_default_shipping = True
    else:
        address.is_default_billing = True


def _ensure_defaults(db: Session, user_id: int) -> None:
    """Every user with an address of a type keeps exactly one default of it."""
    for kind in ("SHIPPING", "BILLING"):
        flag = Address.is_default_shipping if kind == "SHIPPING" else Address.is_default_billing
        if db.query(Address.id).filter(Address.user_id == user_id, flag.is_(True)).first():
            continue
        candidate = (
            db.query(Address)
            .filter(Address.user_id == user_id, Address.address_type.in_((kind, "BOTH")))
            .order_by(Address.id.desc())
            .first()
        )
        if candidate is not None:
            _make_default(db, candidate, kind)


@router.post("", response_model=AddressResponse, status_code=status.HTTP_201_CREATED)
def create_address(
    payload: AddressCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    data = payload.model_dump(exclude={"is_default"})
    address = Address(user_id=current_user.id, **data)
    db.add(address)
    db.flush()

    for kind in ("SHIPPING", "BILLING"):
        if _applies(address, kind) and payload.is_default:
            _make_default(db, address, kind)
    db.flush()
    _ensure_defaults(db, current_user.id)  # first address becomes the default
    db.commit()
    db.refresh(address)
    return address


@router.get("", response_model=list[AddressResponse])
def list_addresses(
    address_type: str | None = Query(default=None, pattern=r"^(SHIPPING|BILLING)$"),
    user_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target_id = current_user.id
    if user_id is not None and user_id != current_user.id:
        if current_user.role not in STAFF_VIEWERS:
            raise HTTPException(status_code=403, detail="Not allowed to view another user's addresses")
        target_id = user_id

    query = db.query(Address).filter(Address.user_id == target_id)
    if address_type:
        query = query.filter(Address.address_type.in_((address_type, "BOTH")))
    return query.order_by(Address.id.asc()).all()


@router.get("/{address_id}", response_model=AddressResponse)
def get_address(
    address_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_owned(db, address_id, current_user)


@router.patch("/{address_id}", response_model=AddressResponse)
def update_address(
    address_id: int,
    payload: AddressUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    address = _get_owned(db, address_id, current_user)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No changes supplied")

    for field, value in changes.items():
        if value is None and field in {"full_name", "phone", "line1", "city", "state", "postal_code", "country", "address_type"}:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")
        setattr(address, field, value)

    # If the type no longer covers a default role, give up that default.
    if not _applies(address, "SHIPPING"):
        address.is_default_shipping = False
    if not _applies(address, "BILLING"):
        address.is_default_billing = False
    db.flush()
    _ensure_defaults(db, current_user.id)
    db.commit()
    db.refresh(address)
    return address


@router.put("/{address_id}/default", response_model=AddressResponse)
def set_default_address(
    address_id: int,
    kind: str = Query(pattern=r"^(SHIPPING|BILLING)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    address = _get_owned(db, address_id, current_user)
    if not _applies(address, kind):
        raise HTTPException(
            status_code=409,
            detail=f"This address is not a {kind.lower()} address",
        )
    _make_default(db, address, kind)
    db.commit()
    db.refresh(address)
    return address


@router.delete("/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_address(
    address_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    address = _get_owned(db, address_id, current_user)
    # Orders keep their own address snapshot, so deleting is always safe.
    db.delete(address)
    db.flush()
    _ensure_defaults(db, current_user.id)
    db.commit()
