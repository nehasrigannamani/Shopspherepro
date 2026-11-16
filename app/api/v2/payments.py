from datetime import timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.core.pagination import PageParams, page_params, paginate
from app.core.ratelimit import rate_limit
from app.db.session import get_db
from app.models.order import Order
from app.models.payment import Payment
from app.models.payment_ops import PaymentException, PaymentReconciliation, PaymentSettlement
from app.models.payment_transaction import PaymentTransaction
from app.models.refund import Refund
from app.models.user import User, UserRole
from app.schemas.payment_ops import (
    ExceptionOut, ExceptionPage, ExceptionResolve, HistoryEvent, PaymentSummary, PaymentSummaryRow,
    ProcessApprovedRequest, ProcessSummary, ReconcileRequest, ReconcileSummary, ReconciliationOut,
    RefundCreate, SettlementCreate, SettlementOut, SettlementPage,
)
from app.schemas.refunds import RefundResponse
from app.services import idempotency
from app.services.audit import write_audit_log
from app.services.payment_exceptions import raise_exception
from app.services.payment_reconciliation import reconcile_payment, reconcile_pending
from app.services.payment_refunds import create_refund_request, process_approved_refunds
from app.services.payment_settlements import expected_totals
from app.services.payments import PAID_STATUSES, committed_refund_total
from app.services.pricing import money
from app.services.reservations import now_naive

R = UserRole
FINANCE = (R.SUPER_ADMIN, R.ADMIN, R.FINANCE_MANAGER)
HISTORY_STAFF = FINANCE + (R.SUPPORT_AGENT,)

payments_router = APIRouter(prefix="/v2/payments", tags=["Payments (v2)"])
refunds_router = APIRouter(prefix="/v2/refunds", tags=["Refunds (v2)"])
finance_router = APIRouter(prefix="/v2/finance", tags=["Finance exceptions & settlements (v2)"])
order_payments_router = APIRouter(prefix="/v2/orders", tags=["Payments (v2)"])

IDEM = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=128,
              description="Optional. Same key + same body replays the first response; a different body returns 422.")


def _naive(dt):
    """Aware datetimes are converted to UTC; naive ones are taken as UTC (how the DB stores them)."""
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ------------------------------------------------------------------ payments
@payments_router.get("/{payment_id}/history", response_model=list[HistoryEvent],
                     summary="Payment history: gateway events, refunds and reconciliations in time order")
def payment_history(payment_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_roles(R.CUSTOMER, *HISTORY_STAFF))):
    payment = db.get(Payment, payment_id)
    if payment is None or (user.role == R.CUSTOMER and payment.user_id != user.id):
        raise HTTPException(status_code=404, detail="Payment not found")
    staff = user.role != R.CUSTOMER
    events: list[tuple] = []

    def add(at, **kw):
        events.append((_naive(at), len(events), HistoryEvent(at=_naive(at), **kw)))

    for t in db.scalars(select(PaymentTransaction).where(PaymentTransaction.payment_id == payment.id)).all():
        add(t.created_at, kind="TRANSACTION", type=t.event_type, status=t.status, amount=t.amount,
            message=t.message, ref_id=t.id)           # raw gateway payloads are never returned
    for r in db.scalars(select(Refund).where(Refund.payment_id == payment.id)).all():
        add(r.created_at, kind="REFUND", type="REFUND", status=r.status, amount=r.amount,
            message=r.reason, ref_id=r.id)
    if staff:
        for c in db.scalars(select(PaymentReconciliation).where(PaymentReconciliation.payment_id == payment.id)).all():
            add(c.created_at, kind="RECONCILIATION", type=c.outcome, status=c.local_status_after,
                message=c.detail, ref_id=c.id)
    events.sort(key=lambda e: (e[0] is None, e[0], e[1]))
    return [e[2] for e in events]


@payments_router.post("/reconcile-pending", response_model=ReconcileSummary,
                      summary="Compare stale PENDING payments with the gateway (times out abandoned ones)",
                      dependencies=[Depends(rate_limit(10))])
def reconcile_pending_endpoint(payload: ReconcileRequest | None = None, db: Session = Depends(get_db),
                               user: User = Depends(require_roles(*FINANCE))):
    payload = payload or ReconcileRequest()
    result = reconcile_pending(db, user.id, payload.older_than_minutes, payload.limit)
    write_audit_log(db, actor_user_id=user.id, action="PAYMENT_RECONCILE_BATCH", entity_type="payment",
                    entity_id=None, details=result, commit=True)
    return result


@payments_router.post("/{payment_id}/reconcile", response_model=ReconciliationOut,
                      summary="Reconcile one payment with the gateway",
                      responses={404: {"description": "Payment not found"}},
                      dependencies=[Depends(rate_limit(30))])
def reconcile_one(payment_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_roles(*FINANCE))):
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    row = reconcile_payment(db, payment, user.id)
    write_audit_log(db, actor_user_id=user.id, action="PAYMENT_RECONCILE", entity_type="payment",
                    entity_id=payment.id, details={"outcome": row.outcome}, commit=True)
    return row


@order_payments_router.get("/{order_id}/payment-summary", response_model=PaymentSummary,
                           summary="Money position of an order: captured, refunded, in progress, still refundable")
def payment_summary(order_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_roles(R.CUSTOMER, *HISTORY_STAFF))):
    order = db.get(Order, order_id)
    if order is None or (user.role == R.CUSTOMER and order.user_id != user.id):
        raise HTTPException(status_code=404, detail="Order not found")
    payments = list(db.scalars(select(Payment).where(Payment.order_id == order.id).order_by(Payment.id)).all())
    refunds = list(db.scalars(select(Refund).where(Refund.order_id == order.id).order_by(Refund.id)).all())
    zero = Decimal("0.00")
    paid = [p for p in payments if p.status in PAID_STATUSES]
    captured = sum((Decimal(str(p.amount)) for p in paid), zero)
    refunded = sum((Decimal(str(r.amount)) for r in refunds if r.status == "SUCCEEDED"), zero)
    in_progress = sum((Decimal(str(r.amount)) for r in refunds if r.status in ("REQUESTED", "APPROVED", "PROCESSING")), zero)
    remaining = sum((Decimal(str(p.amount)) - committed_refund_total(db, p.id) for p in paid), zero)
    return PaymentSummary(
        order_id=order.id, order_total=order.grand_total, currency=order.currency or "INR",
        captured=money(captured), refunded=money(refunded), refunds_in_progress=money(in_progress),
        refundable_remaining=money(remaining),
        payments=[PaymentSummaryRow(id=p.id, status=p.status, amount=p.amount, currency=p.currency,
                                    provider=p.provider) for p in payments],
        refunds=[RefundResponse.model_validate(r) for r in refunds],
    )


# ------------------------------------------------------------------ refunds
@refunds_router.post("", response_model=RefundResponse, status_code=201,
                     summary="Request a (partial) refund; several refunds per payment are allowed up to the paid amount",
                     responses={404: {"description": "Payment not found (or not yours)"},
                                409: {"description": "Not refundable / exceeds remaining amount / key in progress"},
                                422: {"description": "Invalid body or idempotency key reuse"}},
                     dependencies=[Depends(rate_limit(30))])
def request_refund(payload: RefundCreate, idempotency_key: str | None = IDEM, db: Session = Depends(get_db),
                   user: User = Depends(require_roles(R.CUSTOMER, *FINANCE))):
    try:
        begun, replay = idempotency.start(db, scope="refund.create", key=idempotency_key, user_id=user.id,
                                          payload=payload.model_dump(mode="json"))
        if replay is not None:
            return replay
        refund = create_refund_request(db, payload.payment_id, payload.amount, payload.reason, user,
                                       is_staff=user.role in FINANCE)
        body = RefundResponse.model_validate(refund)
        if begun is not None:
            idempotency.complete(db, begun, 201, jsonable_encoder(body))
        db.commit()
        return body
    except Exception:
        db.rollback()
        raise


@refunds_router.post("/process-approved", response_model=ProcessSummary,
                     summary="Retry/process APPROVED refunds at the gateway (also run by the scheduler)",
                     dependencies=[Depends(rate_limit(10))])
def process_approved(payload: ProcessApprovedRequest | None = None, db: Session = Depends(get_db),
                     user: User = Depends(require_roles(*FINANCE))):
    result = process_approved_refunds(db, user.id, (payload or ProcessApprovedRequest()).limit)
    write_audit_log(db, actor_user_id=user.id, action="REFUND_PROCESS_BATCH", entity_type="refund",
                    entity_id=None, details=result, commit=True)
    return result


# ------------------------------------------------------------------ exceptions
def _exception_or_404(db: Session, exception_id: int) -> PaymentException:
    row = db.scalar(select(PaymentException).where(PaymentException.id == exception_id).with_for_update())
    if row is None:
        raise HTTPException(status_code=404, detail="Exception not found")
    return row


@finance_router.get("/exceptions", response_model=ExceptionPage, summary="Exception review queue")
def list_exceptions(
    status: str | None = Query(None, pattern="^(OPEN|IN_REVIEW|RESOLVED|DISMISSED)$"),
    exc_type: str | None = Query(None, max_length=40),
    severity: str | None = Query(None, pattern="^(LOW|MEDIUM|HIGH)$"),
    payment_id: int | None = Query(None, ge=1), order_id: int | None = Query(None, ge=1),
    sort: str = Query("newest", pattern="^(newest|oldest)$"),
    params: PageParams = Depends(page_params), db: Session = Depends(get_db),
    user: User = Depends(require_roles(*FINANCE)),
):
    stmt = select(PaymentException)
    for column, value in ((PaymentException.status, status), (PaymentException.exc_type, exc_type),
                          (PaymentException.severity, severity), (PaymentException.payment_id, payment_id),
                          (PaymentException.order_id, order_id)):
        if value is not None:
            stmt = stmt.where(column == value)
    stmt = stmt.order_by(PaymentException.id.asc() if sort == "oldest" else PaymentException.id.desc())
    rows, total = paginate(db, stmt, params)
    return ExceptionPage(items=rows, total=total, page=params.page, page_size=params.page_size)


@finance_router.get("/exceptions/{exception_id}", response_model=ExceptionOut, summary="One exception")
def get_exception(exception_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*FINANCE))):
    row = db.get(PaymentException, exception_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Exception not found")
    return row


@finance_router.post("/exceptions/{exception_id}/claim", response_model=ExceptionOut,
                     summary="Take an OPEN exception into review", responses={409: {"description": "Not OPEN"}})
def claim_exception(exception_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*FINANCE))):
    try:
        row = _exception_or_404(db, exception_id)
        if row.status != "OPEN":
            raise HTTPException(status_code=409, detail=f"Exception is already {row.status}")
        row.status, row.assigned_to = "IN_REVIEW", user.id
        write_audit_log(db, actor_user_id=user.id, action="PAYMENT_EXCEPTION_CLAIM", entity_type="payment_exception",
                        entity_id=row.id, details={"type": row.exc_type}, commit=False)
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


@finance_router.post("/exceptions/{exception_id}/resolve", response_model=ExceptionOut,
                     summary="Resolve or dismiss an exception (a note is mandatory)",
                     responses={409: {"description": "Already closed"}})
def resolve_exception(exception_id: int, payload: ExceptionResolve, db: Session = Depends(get_db),
                      user: User = Depends(require_roles(*FINANCE))):
    try:
        row = _exception_or_404(db, exception_id)
        if row.status in ("RESOLVED", "DISMISSED"):
            raise HTTPException(status_code=409, detail=f"Exception is already {row.status}")
        row.status, row.resolution_note = payload.resolution, payload.note.strip()
        row.resolved_by, row.resolved_at = user.id, now_naive()
        write_audit_log(db, actor_user_id=user.id, action="PAYMENT_EXCEPTION_" + payload.resolution,
                        entity_type="payment_exception", entity_id=row.id,
                        details={"type": row.exc_type}, commit=False)
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


# ------------------------------------------------------------------ settlements
@finance_router.post("/settlements", response_model=SettlementOut, status_code=201,
                     summary="Record a provider settlement statement and compare it with our ledger",
                     responses={409: {"description": "This statement was already recorded"}},
                     dependencies=[Depends(rate_limit(30))])
def create_settlement(payload: SettlementCreate, db: Session = Depends(get_db),
                      user: User = Depends(require_roles(*FINANCE))):
    start, end = _naive(payload.period_start), _naive(payload.period_end)
    currency = payload.currency.upper()
    try:
        exp_gross, exp_refunds = expected_totals(db, currency, start, end)
        reported_net = money(payload.reported_gross - payload.reported_refunds - payload.reported_fees)
        variance = money((payload.reported_gross - payload.reported_refunds) - (exp_gross - exp_refunds))
        row = PaymentSettlement(
            provider=payload.provider.upper(), provider_reference=payload.provider_reference.strip(),
            currency=currency, period_start=start, period_end=end,
            reported_gross=payload.reported_gross, reported_refunds=payload.reported_refunds,
            reported_fees=payload.reported_fees, reported_net=reported_net,
            expected_gross=exp_gross, expected_refunds=exp_refunds, variance=variance,
            status="MATCHED" if variance == 0 else "VARIANCE", notes=payload.notes, created_by=user.id,
        )
        db.add(row)
        db.flush()
        if variance != 0:
            raise_exception(
                db, "SETTLEMENT_VARIANCE", settlement_id=row.id, key_suffix=str(row.id), severity="HIGH",
                summary=f"Settlement {row.provider_reference} differs from the ledger by {variance} {currency}",
                details={"variance": str(variance), "reported_gross": str(payload.reported_gross),
                         "expected_gross": str(exp_gross), "reported_refunds": str(payload.reported_refunds),
                         "expected_refunds": str(exp_refunds)},
            )
        write_audit_log(db, actor_user_id=user.id, action="SETTLEMENT_RECORDED", entity_type="payment_settlement",
                        entity_id=row.id, details={"status": row.status}, commit=False)
        db.commit()
        db.refresh(row)
        return row
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This settlement statement was already recorded")
    except Exception:
        db.rollback()
        raise


@finance_router.get("/settlements", response_model=SettlementPage, summary="Settlement history")
def list_settlements(
    status: str | None = Query(None, pattern="^(MATCHED|VARIANCE)$"),
    currency: str | None = Query(None, min_length=3, max_length=3),
    params: PageParams = Depends(page_params), db: Session = Depends(get_db),
    user: User = Depends(require_roles(*FINANCE)),
):
    stmt = select(PaymentSettlement)
    if status:
        stmt = stmt.where(PaymentSettlement.status == status)
    if currency:
        stmt = stmt.where(PaymentSettlement.currency == currency.upper())
    rows, total = paginate(db, stmt.order_by(PaymentSettlement.id.desc()), params)
    return SettlementPage(items=rows, total=total, page=params.page, page_size=params.page_size)


@finance_router.get("/settlements/{settlement_id}", response_model=SettlementOut, summary="One settlement")
def get_settlement(settlement_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*FINANCE))):
    row = db.get(PaymentSettlement, settlement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Settlement not found")
    return row
