
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_roles
from app.db.session import get_db
from app.models.order import Order
from app.models.support_ticket import SupportTicket
from app.models.support_ticket_message import SupportTicketMessage
from app.models.user import User
from app.schemas.support_tickets import (
    SupportTicketCreate,
    SupportTicketMessageCreate,
    SupportTicketMessageResponse,
    SupportTicketResponse,
    SupportTicketUpdate,
)


router = APIRouter(
    prefix="/support/tickets",
    tags=["Customer Support Tickets"],
)


STAFF_ROLES = (
    "SUPER_ADMIN",
    "ADMIN",
    "SUPPORT_AGENT",
)


VALID_STATUSES = {
    "OPEN",
    "IN_PROGRESS",
    "WAITING_FOR_CUSTOMER",
    "RESOLVED",
    "CLOSED",
}


def get_ticket_or_404(db: Session, ticket_id: int) -> SupportTicket:
    ticket = (
        db.query(SupportTicket)
        .filter(SupportTicket.id == ticket_id)
        .first()
    )

    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Support ticket not found",
        )

    return ticket


def ensure_ticket_access(
    ticket: SupportTicket,
    current_user: User,
) -> None:
    is_staff = current_user.role in STAFF_ROLES

    if ticket.user_id != current_user.id and not is_staff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this support ticket",
        )


@router.post(
    "",
    response_model=SupportTicketResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_support_ticket(
    payload: SupportTicketCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.order_id is not None:
        order = (
            db.query(Order)
            .filter(
                Order.id == payload.order_id,
                Order.user_id == current_user.id,
            )
            .first()
        )

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found for this customer",
            )

    ticket = SupportTicket(
        ticket_number=f"TKT-{uuid4().hex[:12].upper()}",
        user_id=current_user.id,
        order_id=payload.order_id,
        subject=payload.subject,
        description=payload.description,
        category=payload.category,
        priority=payload.priority,
        status="OPEN",
    )

    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    return ticket


@router.get(
    "/mine",
    response_model=list[SupportTicketResponse],
)
def get_my_support_tickets(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(SupportTicket)
        .filter(SupportTicket.user_id == current_user.id)
        .order_by(SupportTicket.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get(
    "",
    response_model=list[SupportTicketResponse],
)
def list_support_tickets(
    ticket_status: str | None = Query(
        default=None,
        pattern=r"^(OPEN|IN_PROGRESS|WAITING_FOR_CUSTOMER|RESOLVED|CLOSED)$",
    ),
    priority: str | None = Query(
        default=None,
        pattern=r"^(LOW|MEDIUM|HIGH|URGENT)$",
    ),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*STAFF_ROLES)),
):
    query = db.query(SupportTicket)

    if ticket_status:
        query = query.filter(SupportTicket.status == ticket_status)

    if priority:
        query = query.filter(SupportTicket.priority == priority)

    return (
        query.order_by(SupportTicket.created_at.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get(
    "/{ticket_id}",
    response_model=SupportTicketResponse,
)
def get_support_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_access(ticket, current_user)
    return ticket


@router.patch(
    "/{ticket_id}",
    response_model=SupportTicketResponse,
)
def update_support_ticket(
    ticket_id: int,
    payload: SupportTicketUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*STAFF_ROLES)),
):
    ticket = get_ticket_or_404(db, ticket_id)
    updates = payload.model_dump(exclude_unset=True)

    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one field to update",
        )

    new_status = updates.get("status")

    if new_status is not None:
        if ticket.status in {"RESOLVED", "CLOSED"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Resolved or closed tickets cannot be reopened through this endpoint",
            )

        if new_status in {"RESOLVED", "CLOSED"}:
            ticket.resolved_at = datetime.now(timezone.utc)

    if "assigned_to" in updates and updates["assigned_to"] is not None:
        assignee = (
            db.query(User)
            .filter(
                User.id == updates["assigned_to"],
                User.is_active.is_(True),
                User.role.in_(STAFF_ROLES),
            )
            .first()
        )

        if assignee is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assignee must be an active support staff member",
            )

    for field, value in updates.items():
        setattr(ticket, field, value)

    db.commit()
    db.refresh(ticket)

    return ticket


@router.post(
    "/{ticket_id}/messages",
    response_model=SupportTicketMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_ticket_message(
    ticket_id: int,
    payload: SupportTicketMessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_access(ticket, current_user)

    if ticket.status in {"RESOLVED", "CLOSED"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Messages cannot be added to resolved or closed tickets",
        )

    message = SupportTicketMessage(
        ticket_id=ticket.id,
        sender_id=current_user.id,
        message=payload.message,
    )

    db.add(message)

    if current_user.role not in STAFF_ROLES:
        ticket.status = "OPEN"
    elif ticket.status == "WAITING_FOR_CUSTOMER":
        ticket.status = "IN_PROGRESS"

    db.commit()
    db.refresh(message)

    return message


@router.get(
    "/{ticket_id}/messages",
    response_model=list[SupportTicketMessageResponse],
)
def get_ticket_messages(
    ticket_id: int,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ticket = get_ticket_or_404(db, ticket_id)
    ensure_ticket_access(ticket, current_user)

    return (
        db.query(SupportTicketMessage)
        .filter(SupportTicketMessage.ticket_id == ticket.id)
        .order_by(SupportTicketMessage.created_at.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )
