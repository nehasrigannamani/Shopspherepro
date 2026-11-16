from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.refunds import RefundResponse


class RefundCreate(BaseModel):
    payment_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reason: str = Field(min_length=3, max_length=1000)


class ReconcileRequest(BaseModel):
    older_than_minutes: int | None = Field(default=None, ge=0, le=10080)
    limit: int = Field(default=25, ge=1, le=100)


class ProcessApprovedRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


class ReconciliationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payment_id: int
    provider_status: str | None
    local_status_before: str
    local_status_after: str
    outcome: str
    detail: str | None
    triggered_by: int | None
    created_at: datetime


class ReconcileSummary(BaseModel):
    checked: int
    outcomes: dict[str, int]


class ProcessSummary(BaseModel):
    attempted: int
    succeeded: int
    failed: int
    skipped: int


class HistoryEvent(BaseModel):
    at: datetime | None
    kind: Literal["TRANSACTION", "REFUND", "RECONCILIATION"]
    type: str
    status: str | None = None
    amount: Decimal | None = None
    message: str | None = None
    ref_id: int | None = None


class PaymentSummaryRow(BaseModel):
    id: int
    status: str
    amount: Decimal
    currency: str
    provider: str


class PaymentSummary(BaseModel):
    order_id: int
    order_total: Decimal
    currency: str
    captured: Decimal
    refunded: Decimal
    refunds_in_progress: Decimal
    refundable_remaining: Decimal
    payments: list[PaymentSummaryRow]
    refunds: list[RefundResponse]


class ExceptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exc_type: str
    severity: str
    status: str
    payment_id: int | None
    order_id: int | None
    refund_id: int | None
    settlement_id: int | None
    summary: str
    details: dict | None
    assigned_to: int | None
    resolution_note: str | None
    resolved_by: int | None
    resolved_at: datetime | None
    created_at: datetime


class ExceptionPage(BaseModel):
    items: list[ExceptionOut]
    total: int
    page: int
    page_size: int


class ExceptionResolve(BaseModel):
    resolution: Literal["RESOLVED", "DISMISSED"]
    note: str = Field(min_length=3, max_length=2000)


class SettlementCreate(BaseModel):
    provider: str = Field(default="STRIPE", min_length=2, max_length=30)
    provider_reference: str = Field(min_length=3, max_length=100)
    currency: str = Field(min_length=3, max_length=3)
    period_start: datetime
    period_end: datetime
    reported_gross: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    reported_refunds: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    reported_fees: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _check(self):
        def utc(dt: datetime) -> datetime:
            return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt

        if utc(self.period_end) <= utc(self.period_start):
            raise ValueError("period_end must be after period_start")
        if self.reported_refunds + self.reported_fees > self.reported_gross + self.reported_refunds:
            raise ValueError("fees cannot exceed the gross amount")
        return self


class SettlementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    provider_reference: str
    currency: str
    period_start: datetime
    period_end: datetime
    reported_gross: Decimal
    reported_refunds: Decimal
    reported_fees: Decimal
    reported_net: Decimal
    expected_gross: Decimal
    expected_refunds: Decimal
    variance: Decimal
    status: str
    notes: str | None
    created_at: datetime


class SettlementPage(BaseModel):
    items: list[SettlementOut]
    total: int
    page: int
    page_size: int
