# Phase 2 – Slice 1: Foundation + Inventory Reservations

**Status: written and syntax-checked only. NOT executed.** The authoring sandbox had no network, so
FastAPI/SQLAlchemy/pytest could not be installed. Run `alembic upgrade head && pytest` before relying on it.

## Foundation (module 3.13 / 3.12 core)
| Piece | File | Behaviour |
|---|---|---|
| SELLER role | `models/user.py`, `core/permissions.py` | New role; not staff, not customer. Migration restates the MySQL ENUM. |
| Error envelope | `core/errors.py` | Keeps `detail`; adds `error{code,message,request_id,fields}`. Validation errors never echo submitted values. |
| Rate limit | `core/ratelimit.py` | `Depends(rate_limit(N))`, 429 + `Retry-After`. Per process. Config: `RATE_LIMIT_ENABLED`, `RATE_LIMIT_PER_MINUTE`. |
| Log redaction | `core/log_filter.py` | Masks bearer tokens, JWTs, `sk_/whsec_` keys, password/secret/token pairs, card-like numbers. |
| Pagination | `core/pagination.py` | `page>=1`, `page_size<=100`, shared `paginate()`. |
| Idempotency | `models/idempotency.py`, `services/idempotency.py` | Unique (scope,key); replay, 422 on changed body/other user, 409 while in progress. Row is rolled back with the work, so failed calls do not burn the key. |

## Reservations (module 3.6, part)
Endpoints (all under `/v2/inventory/reservations`):

| Method | Path | Roles | Notes |
|---|---|---|---|
| POST | `` | CUSTOMER | Hold cart stock. Optional `Idempotency-Key`. 30/min. TTL default 15 min, capped at 120. |
| GET | `/mine` | CUSTOMER | Paginated, `status` filter |
| POST | `/mine/release` | CUSTOMER | Release all my active holds |
| POST | `/{id}/release` | CUSTOMER (own), inventory staff | 404 if not yours, 409 if not ACTIVE |
| GET | `` | SUPER_ADMIN, ADMIN, INVENTORY_MANAGER | Filters: status, user_id, product_id |
| POST | `/expire` | same staff | Sweep expired holds (also runs in `scheduler_tick`) |

How it works: an ACTIVE hold adds to the stock row's existing `reserved_quantity`, so the existing
availability checks (cart, checkout, reports) honour it. Checkout releases the customer's own holds
first (status `CONVERTED`, `order_id` set; or `EXPIRED` if past expiry), then deducts stock as before.
Lock order: cart -> reservations -> stock rows (sorted by product, variant).

## Changed Phase 1 files
`models/user.py`, `core/permissions.py`, `core/config.py`, `models/__init__.py`, `main.py`,
`api/checkout.py` (3 lines: convert holds), `services/jobs.py` (expiry sweep), `tests/conftest.py`
(rate-limiter reset fixture).

## Known limits
* Not run against MySQL or SQLite; row-lock behaviour is untested (SQLite ignores `FOR UPDATE`).
* Rate limiter and its state are per process.
* A customer's own hold counts against availability in cart-quantity checks until released/converted.
* Reservations are not yet released on payment failure/cancel, because stock is still deducted at
  checkout in Phase 1; this becomes relevant when order flow moves to deduct-on-payment.
* Batch/expiry tracking, warehouse-level availability and stock-discrepancy APIs are not in this slice.
* `postman/` collection not yet extended (`python -m scripts.generate_postman` should pick up the routes).
