# ShopSphere Pro – E-Commerce Backend

FastAPI · MySQL · SQLAlchemy 2 · Alembic · Pydantic v2 · JWT (access + refresh) · RBAC · Stripe (test mode)

Backend only. 50 functional modules, about 245 endpoints, documented in Swagger UI at `/docs`.
See [`docs/MODULE_COVERAGE.md`](docs/MODULE_COVERAGE.md) for the module-by-module map.

## 1. Setup

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env            # then edit DATABASE_URL, SECRET_KEY, Stripe keys
```

Create the database (MySQL 8):

```sql
CREATE DATABASE shopsphere_pro CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'shopsphere_user'@'localhost' IDENTIFIED BY 'CHANGE_ME';
GRANT ALL ON shopsphere_pro.* TO 'shopsphere_user'@'localhost';
```

Apply migrations and load sample data:

```bash
alembic upgrade head
python -m scripts.seed          # users for every role, catalog, stock, shipping, tax, coupon
```

Run:

```bash
uvicorn app.main:app --reload
# Swagger UI  http://localhost:8000/docs      OpenAPI  http://localhost:8000/openapi.json
```

Seeded accounts all use the password `ShopSphere#2026` (local development only):
`superadmin@`, `admin@`, `product.manager@`, `inventory.manager@`, `order.manager@`, `support@`,
`delivery.manager@`, `finance.manager@`, `customer@`, `customer2@` + `shopsphere.test`.

## 2. Tests

```bash
pytest
```

Tests run on an in-memory SQLite database and mock Stripe, so they need no MySQL, network or keys.
SQLite ignores `SELECT … FOR UPDATE`, so **row-lock and true-concurrency behaviour is not exercised by
the test suite** – see "Known limits" below.

## 3. Stripe (test mode)

1. Put your `sk_test_…` key in `STRIPE_SECRET_KEY`.
2. Forward webhooks: `stripe listen --forward-to localhost:8000/payments/webhook`, and put the printed
   `whsec_…` value in `STRIPE_WEBHOOK_SECRET`.
3. `POST /payments/orders/{id}/checkout-session` → open `checkout_url` → pay with `4242 4242 4242 4242`.
4. The webhook marks the payment `SUCCEEDED`, the order `PAID`/`CONFIRMED` and notifies the customer.
   If webhooks cannot reach your machine, `POST /payments/{id}/sync` pulls the state from Stripe using the
   same idempotent code path.

## 4. Postman

Import `postman/ShopSphere_Pro.postman_collection.json` and `postman/ShopSphere_Local.postman_environment.json`.
Run **Core Workflow** top to bottom (after seeding). **Error Handling** shows 401/403/404/409/422 responses.
**All Endpoints** lists every route (regenerate with `python -m scripts.generate_postman`).

## 5. Key design points

| Concern | Approach |
|---|---|
| Auth | Short-lived access JWT bound to a server-side session; single-use rotating refresh tokens; reuse of a spent refresh token revokes the session; logout/deactivation/role change/password change revoke sessions immediately |
| RBAC | `require_roles(...)` on every protected route + ownership checks (other customers' resources return 404) |
| Pricing | One server-side engine (`app/services/pricing.py`) used by cart preview **and** checkout: promotions, coupon, tax (inclusive/exclusive, category/region), shipping, free-shipping threshold |
| Checkout | Cart row locked → prices recalculated → stock deducted with movement records → order + item snapshots (product, SKU, variant, price) → cart cleared, all in one transaction. A repeated call finds an empty cart (409) |
| Payments | Hosted Stripe Checkout; webhook signature verified; events de-duplicated by unique event id + row lock; amount/currency verified; reused open session instead of duplicates |
| Refunds | Request → approve → process through Stripe with a stable idempotency key; refunded totals capped at the paid amount; cancellation of a paid order auto-creates and processes a refund |
| Notifications | Event-driven (order, payment, refund, status, low stock, deactivation) → in-app + queued email/SMS; one send attempt per call, exponential backoff, capped retries |
| Audit | Middleware logs every successful/failed/denied write by an authenticated user (actor, route, entity id, status, IP, request id); no update/delete API; MySQL triggers make `audit_logs` append-only |
| Background | `BackgroundJob` table for exports and notification dispatch; scheduled summaries; retry with attempt cap; optional in-process scheduler (`SCHEDULER_ENABLED=true`) or call `POST /report-exports/scheduler/run` from cron |

## 6. Known limits (read before production)

* **Not executed against MySQL by the author of this change set.** Migrations, row locking and the
  full test suite should be run on your machine (`alembic upgrade head && pytest`) before relying on them.
* Stock is deducted at order placement (and restored on cancellation); `reserved_quantity` is honoured
  when checking availability but checkout does not use it as a separate reservation stage.
* The in-process scheduler runs in every API worker; with several workers use cron + the scheduler endpoint instead.
* Files are stored on local disk (`EXPORT_DIR`, product media); use object storage in production.
* SMS needs a provider URL/token that matches the simple JSON contract in `app/services/delivery.py`.
* Rotate any credentials that were ever committed (the originally supplied zip contained a `.env`).
