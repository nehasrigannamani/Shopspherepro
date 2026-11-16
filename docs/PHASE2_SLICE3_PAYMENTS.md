# Phase 2 – Slice 3: Advanced Payments and Refunds (module 3.2)

**Status: written and syntax/name-checked only. NOT executed** (no network in the authoring sandbox).
Run `alembic upgrade head && pytest tests/test_phase2_payments.py`. Requires Slices 1 and 2.

## What Phase 1 already did (unchanged)
Webhook signature check; event-id de-duplication; success / failed / expired / amount-mismatch handling;
partial refunds with a balance cap; refund approval; idempotent gateway refund (`refund-{id}` key); pending-session reuse.

## What this slice adds
| Requirement | Implementation |
|---|---|
| Reconcile with provider | `POST /v2/payments/{id}/reconcile`, `POST /v2/payments/reconcile-pending`; every run is stored in `payment_reconciliations` |
| Timed-out payments | PENDING older than `PAYMENT_TIMEOUT_MINUTES` (default 60) and still open at the gateway -> session expired at Stripe, payment `TIMED_OUT`, customer may retry. A late "paid" callback or reconciliation still applies. |
| Duplicate capture | Second paid payment for an already-paid order becomes `DUPLICATE_PAID`, a full refund is queued (APPROVED), exception raised. The order's own payment state is not touched. Repeated events never queue a second refund. |
| Duplicate refunds | `POST /v2/refunds` with `Idempotency-Key`; payment row locked so concurrent requests cannot overspend the balance |
| Multiple partial refunds | cap = paid amount minus all REQUESTED/APPROVED/PROCESSING/SUCCEEDED refunds; `GET /v2/orders/{id}/payment-summary` shows captured / refunded / in progress / remaining |
| Retry of stuck refunds | `POST /v2/refunds/process-approved` and the scheduler tick |
| Histories | `GET /v2/payments/{id}/history` (gateway events + refunds + reconciliations; raw payloads never returned; reconciliations staff-only) |
| Exception review | `payment_exceptions` queue: `AMOUNT_MISMATCH`, `DUPLICATE_CAPTURE`, `LATE_PAYMENT_AFTER_CANCEL`, `STATUS_DIVERGENCE`, `REFUND_FAILED`, `SETTLEMENT_VARIANCE`. `GET /v2/finance/exceptions` (filters, paging), `GET /{id}`, `POST /{id}/claim`, `POST /{id}/resolve` (note mandatory). Each anomaly is raised once (dedupe key). |
| Settlement history | `POST/GET /v2/finance/settlements`: statement totals are compared with our ledger; a difference marks the record `VARIANCE` and raises an exception |

Reconciliation never changes money already recorded as received. If we say paid but the provider says unpaid, that is
raised as `STATUS_DIVERGENCE` for a human.

## Roles
Finance endpoints: SUPER_ADMIN, ADMIN, FINANCE_MANAGER. History / payment-summary: those plus SUPPORT_AGENT, and the
owning customer. `POST /v2/refunds`: owning customer or finance roles. Everyone else gets 403; other customers' payments give 404.

## Changed Phase 1 code (`services/payments.py` etc.)
* New status `TIMED_OUT` (event `payment.timed_out`, raised only by our own reconciliation) and `DUPLICATE_PAID`.
* Late success/failure events on a REFUNDED or DUPLICATE_PAID payment are now ignored instead of re-applied.
* `settle_refund_totals`: refunding a duplicate no longer marks the order refunded.
* Hooks that raise exceptions: amount mismatch, payment after cancellation, refund failure.
* `stripe_gateway.expire_checkout_session`, `config.payment_timeout_minutes`, scheduler pass.

## Known limits
* Not executed; row-lock behaviour untested. Settlement totals are entered by finance (no Stripe payout import).
* Timeout age uses `payments.created_at` (DB clock) against UTC; keep the DB server on UTC.
* The ledger-based settlement window uses transaction creation time, not the provider's payout cut-off, so statements
  that straddle midnight in another time zone may show a variance until the period is aligned.
* `REFUND_FAILED` exception path and the scheduler pass are not covered by automated tests.
* v2 refund errors use 404/409 (v1 uses 400/403). Postman collection not extended yet.
