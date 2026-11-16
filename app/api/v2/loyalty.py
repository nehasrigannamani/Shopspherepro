from datetime import timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.core.config import settings
from app.core.pagination import PageParams, page_params, paginate
from app.core.ratelimit import rate_limit
from app.db.session import get_db
from app.models.order import Order
from app.models.pricing_loyalty import CustomerSegment, LoyaltyLedger, LoyaltyRule
from app.models.category import Category
from app.models.user import User, UserRole
from app.schemas.pricing_loyalty import (
    CreditResult, ExpireResult, LedgerEntryOut, LedgerPage, LoyaltyAccount, PointsAdjust, RuleCreate, RuleOut,
    RulePage, RuleUpdate,
)
from app.services import loyalty
from app.services.audit import write_audit_log

R = UserRole
ADMINS = (R.SUPER_ADMIN, R.ADMIN)
STAFF_READ = ADMINS + (R.SUPPORT_AGENT, R.FINANCE_MANAGER)
RULE_READ = ADMINS + (R.FINANCE_MANAGER,)

router = APIRouter(prefix="/v2/loyalty", tags=["Loyalty (v2)"])


def _naive(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt is not None and dt.tzinfo else dt


def _account(db: Session, user_id: int) -> LoyaltyAccount:
    loyalty.expire_due(db, user_id)           # lazy expiry keeps the numbers honest
    db.commit()
    earned = db.scalar(select(func.coalesce(func.sum(LoyaltyLedger.points), 0)).where(
        LoyaltyLedger.user_id == user_id, LoyaltyLedger.entry_type == "EARN")) or 0
    return LoyaltyAccount(
        user_id=user_id, balance=loyalty.balance(db, user_id), available_points=loyalty.available_points(db, user_id),
        expiring_within_30_days=loyalty.expiring_soon(db, user_id, 30), lifetime_earned=int(earned),
        point_value=Decimal(str(settings.loyalty_point_value)), min_redeem_points=settings.loyalty_min_redeem_points,
        max_redeem_percent=settings.loyalty_max_redeem_percent)


def _ledger(db: Session, user_id: int, entry_type: str | None, params: PageParams) -> LedgerPage:
    stmt = select(LoyaltyLedger).where(LoyaltyLedger.user_id == user_id)
    if entry_type:
        stmt = stmt.where(LoyaltyLedger.entry_type == entry_type)
    rows, total = paginate(db, stmt.order_by(LoyaltyLedger.id.desc()), params)
    return LedgerPage(items=rows, total=total, page=params.page, page_size=params.page_size)


ENTRY_TYPES = "^(EARN|REDEEM|EXPIRE|REVERSE_EARN|REVERSE_REDEEM|ADJUST)$"


# ------------------------------------------------------------------ customer
@router.get("/me", response_model=LoyaltyAccount, summary="My points balance")
def my_account(db: Session = Depends(get_db), user: User = Depends(require_roles(R.CUSTOMER))):
    return _account(db, user.id)


@router.get("/me/transactions", response_model=LedgerPage, summary="My points history")
def my_transactions(entry_type: str | None = Query(None, pattern=ENTRY_TYPES), params: PageParams = Depends(page_params),
                    db: Session = Depends(get_db), user: User = Depends(require_roles(R.CUSTOMER))):
    return _ledger(db, user.id, entry_type, params)


# ------------------------------------------------------------------ staff
def _customer_or_404(db: Session, user_id: int) -> User:
    member = db.get(User, user_id)
    if member is None or member.role != UserRole.CUSTOMER:
        raise HTTPException(status_code=404, detail="Customer not found")
    return member


@router.get("/users/{user_id}", response_model=LoyaltyAccount, summary="A customer's points balance (staff)")
def user_account(user_id: int, db: Session = Depends(get_db), staff: User = Depends(require_roles(*STAFF_READ))):
    _customer_or_404(db, user_id)
    return _account(db, user_id)


@router.get("/users/{user_id}/transactions", response_model=LedgerPage, summary="A customer's points history (staff)")
def user_transactions(user_id: int, entry_type: str | None = Query(None, pattern=ENTRY_TYPES),
                      params: PageParams = Depends(page_params), db: Session = Depends(get_db),
                      staff: User = Depends(require_roles(*STAFF_READ))):
    _customer_or_404(db, user_id)
    return _ledger(db, user_id, entry_type, params)


@router.post("/users/{user_id}/adjust", response_model=LedgerEntryOut, status_code=201,
             summary="Manually add or remove points (audited)",
             responses={404: {"description": "Customer not found"}, 409: {"description": "Not enough points to remove"}},
             dependencies=[Depends(rate_limit(30))])
def adjust_points(user_id: int, payload: PointsAdjust,
                  idempotency_key: str | None = Header(None, alias="Idempotency-Key", min_length=8, max_length=100),
                  db: Session = Depends(get_db), staff: User = Depends(require_roles(*ADMINS))):
    _customer_or_404(db, user_id)
    try:
        row, created = loyalty.adjust(db, user_id, payload.points, payload.reason.strip(), staff.id,
                                      key=f"adjust:{user_id}:{idempotency_key}" if idempotency_key else None)
        if created:
            write_audit_log(db, actor_user_id=staff.id, action="LOYALTY_ADJUSTMENT", entity_type="user",
                            entity_id=user_id, details={"points": payload.points, "reason": payload.reason},
                            commit=False)
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


@router.post("/expire", response_model=ExpireResult, summary="Expire all points past their expiry date now")
def expire_now(db: Session = Depends(get_db), staff: User = Depends(require_roles(*ADMINS))):
    count = loyalty.expire_due(db)
    write_audit_log(db, actor_user_id=staff.id, action="LOYALTY_EXPIRY_RUN", entity_type="loyalty",
                    entity_id=None, details={"lots": count}, commit=False)
    db.commit()
    return ExpireResult(expired_lots=count)


@router.post("/orders/{order_id}/credit", response_model=CreditResult,
             summary="Credit earned points for a delivered, paid order (idempotent catch-up)",
             responses={404: {"description": "Order not found"}, 409: {"description": "Order is not eligible"}})
def credit_order(order_id: int, db: Session = Depends(get_db),
                 staff: User = Depends(require_roles(*ADMINS, R.FINANCE_MANAGER))):
    try:
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        if str(order.status).upper() != "DELIVERED" or str(order.payment_status).upper() not in loyalty.EARN_ELIGIBLE_PAYMENT:
            raise HTTPException(status_code=409, detail="Only delivered, paid orders earn points")
        already = db.scalar(select(LoyaltyLedger.id).where(
            LoyaltyLedger.idempotency_key == f"earn:{order.id}")) is not None
        points = 0 if already else loyalty.credit_order_points(db, order, staff.id)
        if points:
            write_audit_log(db, actor_user_id=staff.id, action="LOYALTY_MANUAL_CREDIT", entity_type="order",
                            entity_id=order.id, details={"points": points}, commit=False)
        db.commit()
        return CreditResult(order_id=order.id, points_credited=points, already_credited=already)
    except Exception:
        db.rollback()
        raise


# ------------------------------------------------------------------ earning rules
def _check_refs(db: Session, category_id, segment_id) -> None:
    if category_id is not None and db.get(Category, category_id) is None:
        raise HTTPException(status_code=404, detail="Category not found")
    if segment_id is not None and db.get(CustomerSegment, segment_id) is None:
        raise HTTPException(status_code=404, detail="Segment not found")


@router.post("/rules", response_model=RuleOut, status_code=201, summary="Create an earning rule")
def create_rule(payload: RuleCreate, db: Session = Depends(get_db), staff: User = Depends(require_roles(*ADMINS))):
    _check_refs(db, payload.category_id, payload.segment_id)
    try:
        data = payload.model_dump()
        data["starts_at"], data["ends_at"] = _naive(payload.starts_at), _naive(payload.ends_at)
        rule = LoyaltyRule(**data, created_by=staff.id)
        db.add(rule)
        db.flush()
        write_audit_log(db, actor_user_id=staff.id, action="LOYALTY_RULE_CREATED", entity_type="loyalty_rule",
                        entity_id=rule.id, details={"earn_rate": str(payload.earn_rate)}, commit=False)
        db.commit()
        db.refresh(rule)
        return rule
    except Exception:
        db.rollback()
        raise


@router.get("/rules", response_model=RulePage, summary="List earning rules")
def list_rules(is_active: bool | None = None, params: PageParams = Depends(page_params),
               db: Session = Depends(get_db), staff: User = Depends(require_roles(*RULE_READ))):
    stmt = select(LoyaltyRule)
    if is_active is not None:
        stmt = stmt.where(LoyaltyRule.is_active.is_(is_active))
    rows, total = paginate(db, stmt.order_by(LoyaltyRule.priority.desc(), LoyaltyRule.id.desc()), params)
    return RulePage(items=rows, total=total, page=params.page, page_size=params.page_size)


@router.get("/rules/{rule_id}", response_model=RuleOut, summary="One earning rule")
def get_rule(rule_id: int, db: Session = Depends(get_db), staff: User = Depends(require_roles(*RULE_READ))):
    rule = db.get(LoyaltyRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.patch("/rules/{rule_id}", response_model=RuleOut, summary="Change an earning rule (explicit null clears optional fields)")
def update_rule(rule_id: int, payload: RuleUpdate, db: Session = Depends(get_db),
                staff: User = Depends(require_roles(*ADMINS))):
    if not payload.model_fields_set:
        raise HTTPException(status_code=422, detail="No fields to update")
    try:
        rule = db.scalar(select(LoyaltyRule).where(LoyaltyRule.id == rule_id).with_for_update())
        if rule is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        _check_refs(db, payload.category_id if "category_id" in payload.model_fields_set else None,
                    payload.segment_id if "segment_id" in payload.model_fields_set else None)
        required = {"name", "earn_rate", "min_order_value", "priority", "is_active"}
        changed = []
        for field in payload.model_fields_set:
            value = getattr(payload, field)
            if value is None and field in required:
                raise HTTPException(status_code=422, detail=f"{field} cannot be null")
            if field in {"starts_at", "ends_at"}:
                value = _naive(value)
            setattr(rule, field, value)
            changed.append(field)
        if rule.starts_at and rule.ends_at and rule.ends_at <= rule.starts_at:
            raise HTTPException(status_code=422, detail="ends_at must be after starts_at")
        write_audit_log(db, actor_user_id=staff.id, action="LOYALTY_RULE_UPDATED", entity_type="loyalty_rule",
                        entity_id=rule.id, details={"fields": changed}, commit=False)
        db.commit()
        db.refresh(rule)
        return rule
    except Exception:
        db.rollback()
        raise


@router.delete("/rules/{rule_id}", response_model=RuleOut, summary="Deactivate an earning rule (history is kept)")
def deactivate_rule(rule_id: int, db: Session = Depends(get_db), staff: User = Depends(require_roles(*ADMINS))):
    rule = db.get(LoyaltyRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule.is_active = False
    write_audit_log(db, actor_user_id=staff.id, action="LOYALTY_RULE_DEACTIVATED", entity_type="loyalty_rule",
                    entity_id=rule.id, details=None, commit=False)
    db.commit()
    db.refresh(rule)
    return rule
