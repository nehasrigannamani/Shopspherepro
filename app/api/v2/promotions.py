from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.core.pagination import PageParams, page_params, paginate
from app.db.session import get_db
from app.models.coupon import Coupon
from app.models.pricing_loyalty import CustomerSegment, CustomerSegmentMember, PromotionRedemption
from app.models.promotion import Promotion
from app.models.user import User, UserRole
from app.schemas.pricing_loyalty import (
    CouponRulesUpdate, CouponV2Out, MemberAdd, MemberOut, MemberPage, PromotionPage, PromotionRulesUpdate,
    PromotionV2Out, RedemptionPage, SegmentCreate, SegmentOut, SegmentPage,
)
from app.services.audit import write_audit_log

R = UserRole
MANAGERS = (R.SUPER_ADMIN, R.ADMIN, R.PRODUCT_MANAGER)
SEGMENT_WRITE = (R.SUPER_ADMIN, R.ADMIN)
SEGMENT_READ = MANAGERS + (R.SUPPORT_AGENT,)

promotions_router = APIRouter(prefix="/v2/promotions", tags=["Promotions (v2)"])
coupons_router = APIRouter(prefix="/v2/coupons", tags=["Coupons (v2)"])
segments_router = APIRouter(prefix="/v2/segments", tags=["Customer segments (v2)"])


def _segment_must_exist(db: Session, segment_id: int | None) -> None:
    if segment_id is not None and db.get(CustomerSegment, segment_id) is None:
        raise HTTPException(status_code=404, detail="Segment not found")


def _apply_rules(row, payload, db: Session, allowed: set[str]) -> list[str]:
    changed = []
    for field in payload.model_fields_set:
        if field not in allowed:
            continue
        value = getattr(payload, field)
        if value is None and field in {"priority", "stackable", "allow_with_coupon"}:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")
        if field == "customer_segment_id":
            _segment_must_exist(db, value)
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed.append(field)
    return changed


# ------------------------------------------------------------------ promotions
@promotions_router.get("", response_model=PromotionPage, summary="List promotions with their pricing rules")
def list_promotions(
    is_active: bool | None = None,
    target_type: str | None = Query(None, pattern="^(ALL|PRODUCT|CATEGORY)$"),
    valid_now: bool | None = Query(None, description="Only promotions inside their validity window"),
    segment_id: int | None = Query(None, ge=1),
    params: PageParams = Depends(page_params), db: Session = Depends(get_db),
    user: User = Depends(require_roles(*MANAGERS)),
):
    stmt = select(Promotion)
    if is_active is not None:
        stmt = stmt.where(Promotion.is_active.is_(is_active))
    if target_type:
        stmt = stmt.where(Promotion.target_type == target_type)
    if segment_id:
        stmt = stmt.where(Promotion.customer_segment_id == segment_id)
    if valid_now:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stmt = stmt.where(Promotion.starts_at <= now, Promotion.expires_at > now)
    rows, total = paginate(db, stmt.order_by(Promotion.priority.desc(), Promotion.id.desc()), params)
    return PromotionPage(items=rows, total=total, page=params.page, page_size=params.page_size)


@promotions_router.patch("/{promotion_id}/rules", response_model=PromotionV2Out,
                         summary="Set priority, stacking, caps, minimum cart value, usage limits and segment",
                         responses={404: {"description": "Promotion or segment not found"},
                                    422: {"description": "Invalid rule values"}})
def update_promotion_rules(promotion_id: int, payload: PromotionRulesUpdate, db: Session = Depends(get_db),
                           user: User = Depends(require_roles(*MANAGERS))):
    if not payload.model_fields_set:
        raise HTTPException(status_code=422, detail="No rules to update")
    try:
        promo = db.scalar(select(Promotion).where(Promotion.id == promotion_id).with_for_update())
        if promo is None:
            raise HTTPException(status_code=404, detail="Promotion not found")
        changed = _apply_rules(promo, payload, db, {
            "priority", "min_cart_value", "max_discount", "stackable", "allow_with_coupon",
            "per_customer_limit", "customer_segment_id"})
        if changed:
            write_audit_log(db, actor_user_id=user.id, action="PROMOTION_RULES_UPDATED", entity_type="promotion",
                            entity_id=promo.id, details={"fields": changed}, commit=False)
        db.commit()
        db.refresh(promo)
        return promo
    except Exception:
        db.rollback()
        raise


@promotions_router.get("/{promotion_id}/redemptions", response_model=RedemptionPage,
                       summary="Where and when a promotion was used")
def promotion_redemptions(promotion_id: int, status: str | None = Query(None, pattern="^(APPLIED|REVERSED)$"),
                          user_id: int | None = Query(None, ge=1), params: PageParams = Depends(page_params),
                          db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    if db.get(Promotion, promotion_id) is None:
        raise HTTPException(status_code=404, detail="Promotion not found")
    stmt = select(PromotionRedemption).where(PromotionRedemption.promotion_id == promotion_id)
    if status:
        stmt = stmt.where(PromotionRedemption.status == status)
    if user_id:
        stmt = stmt.where(PromotionRedemption.user_id == user_id)
    rows, total = paginate(db, stmt.order_by(PromotionRedemption.id.desc()), params)
    return RedemptionPage(items=rows, total=total, page=params.page, page_size=params.page_size)


# ------------------------------------------------------------------ coupons
@coupons_router.patch("/{coupon_id}/rules", response_model=CouponV2Out,
                      summary="Set a coupon's maximum discount, per-customer limit and segment")
def update_coupon_rules(coupon_id: int, payload: CouponRulesUpdate, db: Session = Depends(get_db),
                        user: User = Depends(require_roles(*MANAGERS))):
    if not payload.model_fields_set:
        raise HTTPException(status_code=422, detail="No rules to update")
    try:
        coupon = db.scalar(select(Coupon).where(Coupon.id == coupon_id).with_for_update())
        if coupon is None:
            raise HTTPException(status_code=404, detail="Coupon not found")
        changed = _apply_rules(coupon, payload, db, {"max_discount", "per_customer_limit", "customer_segment_id"})
        if changed:
            write_audit_log(db, actor_user_id=user.id, action="COUPON_RULES_UPDATED", entity_type="coupon",
                            entity_id=coupon.id, details={"fields": changed}, commit=False)
        db.commit()
        db.refresh(coupon)
        return coupon
    except Exception:
        db.rollback()
        raise


@coupons_router.get("/{coupon_id}/redemptions", response_model=RedemptionPage, summary="Coupon redemption records")
def coupon_redemptions(coupon_id: int, status: str | None = Query(None, pattern="^(APPLIED|REVERSED)$"),
                       user_id: int | None = Query(None, ge=1), params: PageParams = Depends(page_params),
                       db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    if db.get(Coupon, coupon_id) is None:
        raise HTTPException(status_code=404, detail="Coupon not found")
    stmt = select(PromotionRedemption).where(PromotionRedemption.coupon_id == coupon_id)
    if status:
        stmt = stmt.where(PromotionRedemption.status == status)
    if user_id:
        stmt = stmt.where(PromotionRedemption.user_id == user_id)
    rows, total = paginate(db, stmt.order_by(PromotionRedemption.id.desc()), params)
    return RedemptionPage(items=rows, total=total, page=params.page, page_size=params.page_size)


# ------------------------------------------------------------------ segments
@segments_router.post("", response_model=SegmentOut, status_code=201, summary="Create a customer segment",
                      responses={409: {"description": "Code already exists"}})
def create_segment(payload: SegmentCreate, db: Session = Depends(get_db),
                   user: User = Depends(require_roles(*SEGMENT_WRITE))):
    try:
        seg = CustomerSegment(code=payload.code.upper(), name=payload.name.strip(),
                              description=payload.description, created_by=user.id)
        db.add(seg)
        db.flush()
        write_audit_log(db, actor_user_id=user.id, action="SEGMENT_CREATED", entity_type="customer_segment",
                        entity_id=seg.id, details={"code": seg.code}, commit=False)
        db.commit()
        db.refresh(seg)
        return seg
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A segment with this code already exists")
    except Exception:
        db.rollback()
        raise


@segments_router.get("", response_model=SegmentPage, summary="List segments")
def list_segments(params: PageParams = Depends(page_params), db: Session = Depends(get_db),
                  user: User = Depends(require_roles(*SEGMENT_READ))):
    rows, total = paginate(db, select(CustomerSegment).order_by(CustomerSegment.id), params)
    return SegmentPage(items=rows, total=total, page=params.page, page_size=params.page_size)


def _segment_or_404(db: Session, segment_id: int) -> CustomerSegment:
    seg = db.get(CustomerSegment, segment_id)
    if seg is None:
        raise HTTPException(status_code=404, detail="Segment not found")
    return seg


@segments_router.get("/{segment_id}/members", response_model=MemberPage, summary="Segment members")
def list_members(segment_id: int, params: PageParams = Depends(page_params), db: Session = Depends(get_db),
                 user: User = Depends(require_roles(*SEGMENT_READ))):
    _segment_or_404(db, segment_id)
    stmt = select(CustomerSegmentMember).where(CustomerSegmentMember.segment_id == segment_id)
    rows, total = paginate(db, stmt.order_by(CustomerSegmentMember.id), params)
    return MemberPage(items=rows, total=total, page=params.page, page_size=params.page_size)


@segments_router.post("/{segment_id}/members", response_model=MemberOut, status_code=201,
                      summary="Add a customer to a segment",
                      responses={404: {"description": "Segment or customer not found"},
                                 409: {"description": "Already a member"}})
def add_member(segment_id: int, payload: MemberAdd, db: Session = Depends(get_db),
               user: User = Depends(require_roles(*SEGMENT_WRITE))):
    seg = _segment_or_404(db, segment_id)
    member = db.get(User, payload.user_id)
    if member is None or member.role != UserRole.CUSTOMER:
        raise HTTPException(status_code=404, detail="Customer not found")
    try:
        row = CustomerSegmentMember(segment_id=seg.id, user_id=member.id, added_by=user.id)
        db.add(row)
        db.flush()
        write_audit_log(db, actor_user_id=user.id, action="SEGMENT_MEMBER_ADDED", entity_type="customer_segment",
                        entity_id=seg.id, details={"user_id": member.id}, commit=False)
        db.commit()
        db.refresh(row)
        return row
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Customer is already in this segment")
    except Exception:
        db.rollback()
        raise


@segments_router.delete("/{segment_id}/members/{user_id}", status_code=204, summary="Remove a customer from a segment")
def remove_member(segment_id: int, user_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_roles(*SEGMENT_WRITE))):
    row = db.scalar(select(CustomerSegmentMember).where(
        CustomerSegmentMember.segment_id == segment_id, CustomerSegmentMember.user_id == user_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Membership not found")
    db.delete(row)
    write_audit_log(db, actor_user_id=user.id, action="SEGMENT_MEMBER_REMOVED", entity_type="customer_segment",
                    entity_id=segment_id, details={"user_id": user_id}, commit=False)
    db.commit()
