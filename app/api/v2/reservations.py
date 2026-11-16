from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.core.pagination import PageParams, page_params, paginate
from app.core.ratelimit import rate_limit
from app.db.session import get_db
from app.models.inventory_reservation import InventoryReservation
from app.models.user import User, UserRole
from app.schemas.reservations import (
    ExpiryRunResponse,
    ReservationCreate,
    ReservationGroupResponse,
    ReservationPage,
    ReservationResponse,
)
from app.services import idempotency
from app.services.reservations import (
    expire_due_reservations,
    release_reservation,
    release_user_holds,
    reserve_cart,
)

router = APIRouter(prefix="/v2/inventory/reservations", tags=["Inventory Reservations (v2)"])

INVENTORY_ROLES = (UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.INVENTORY_MANAGER)
CUSTOMER_ONLY = (UserRole.CUSTOMER,)


@router.post(
    "",
    response_model=ReservationGroupResponse,
    status_code=201,
    summary="Reserve the current cart's stock",
    description=(
        "Holds stock for every cart line until `expires_at`. Calling again replaces the "
        "caller's earlier holds. Send an `Idempotency-Key` header to make retries safe: "
        "the same key and body replays the original response; the same key with a "
        "different body returns 422."
    ),
    responses={409: {"description": "Cart empty, insufficient stock, or key in progress"},
               422: {"description": "Invalid body or idempotency key reuse"},
               429: {"description": "Rate limit exceeded"}},
    dependencies=[Depends(rate_limit(30))],
)
def create_reservation(
    payload: ReservationCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=128),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*CUSTOMER_ONLY)),
):
    begun = None
    try:
        if idempotency_key:
            outcome = idempotency.begin(
                db, scope="reservation.create", key=idempotency_key,
                user_id=current_user.id, payload=payload.model_dump(),
            )
            if isinstance(outcome, idempotency.Begun):
                begun = outcome
            else:
                return JSONResponse(outcome.response_body, status_code=outcome.response_status,
                                    headers={"Idempotent-Replay": "true"})

        created = reserve_cart(db, current_user.id, payload.ttl_minutes)
        body = ReservationGroupResponse(
            expires_at=created[0].expires_at,
            reservations=[ReservationResponse.model_validate(r) for r in created],
        )
        if begun is not None:
            idempotency.complete(db, begun, 201, jsonable_encoder(body))
        db.commit()
        return body
    except Exception:
        db.rollback()
        raise


@router.get("/mine", response_model=ReservationPage, summary="My reservations")
def my_reservations(
    status: str | None = Query(None, pattern="^(ACTIVE|CONVERTED|RELEASED|EXPIRED)$"),
    params: PageParams = Depends(page_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*CUSTOMER_ONLY)),
):
    stmt = select(InventoryReservation).where(InventoryReservation.user_id == current_user.id)
    if status:
        stmt = stmt.where(InventoryReservation.status == status)
    stmt = stmt.order_by(InventoryReservation.id.desc())
    rows, total = paginate(db, stmt, params)
    return ReservationPage(items=rows, total=total, page=params.page, page_size=params.page_size)


@router.post("/mine/release", response_model=ExpiryRunResponse, summary="Release all my active holds")
def release_my_holds(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*CUSTOMER_ONLY)),
):
    try:
        count = release_user_holds(db, current_user.id, "Released by customer")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ExpiryRunResponse(released=count)


@router.get("", response_model=ReservationPage, summary="List reservations (inventory staff)")
def list_reservations(
    status: str | None = Query(None, pattern="^(ACTIVE|CONVERTED|RELEASED|EXPIRED)$"),
    user_id: int | None = Query(None, ge=1),
    product_id: int | None = Query(None, ge=1),
    params: PageParams = Depends(page_params),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_ROLES)),
):
    stmt = select(InventoryReservation)
    if status:
        stmt = stmt.where(InventoryReservation.status == status)
    if user_id:
        stmt = stmt.where(InventoryReservation.user_id == user_id)
    if product_id:
        stmt = stmt.where(InventoryReservation.product_id == product_id)
    stmt = stmt.order_by(InventoryReservation.id.desc())
    rows, total = paginate(db, stmt, params)
    return ReservationPage(items=rows, total=total, page=params.page, page_size=params.page_size)


@router.post("/expire", response_model=ExpiryRunResponse, summary="Release all expired holds now")
def run_expiry(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*INVENTORY_ROLES)),
):
    return ExpiryRunResponse(released=expire_due_reservations(db))


@router.post(
    "/{reservation_id}/release",
    response_model=ReservationResponse,
    summary="Release one reservation",
    responses={404: {"description": "Not found (or not yours)"},
               409: {"description": "Reservation is not active"}},
)
def release_one(
    reservation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CUSTOMER, *INVENTORY_ROLES)),
):
    try:
        res = db.scalar(
            select(InventoryReservation).where(InventoryReservation.id == reservation_id).with_for_update()
        )
        is_staff = current_user.role in INVENTORY_ROLES
        if res is None or (res.user_id != current_user.id and not is_staff):
            raise HTTPException(status_code=404, detail="Reservation not found")
        if not release_reservation(db, res, "Released by " + ("staff" if is_staff else "customer")):
            raise HTTPException(status_code=409, detail=f"Reservation is already {res.status}")
        db.commit()
        db.refresh(res)
        return res
    except Exception:
        db.rollback()
        raise
