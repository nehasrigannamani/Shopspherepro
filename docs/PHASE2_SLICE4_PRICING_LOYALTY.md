# Phase 2 – Slice 4: Promotions, Pricing Rules and Loyalty (modules 3.3, 3.4)

**Status: written and syntax/name/import-cycle-checked only. NOT executed** (no network in the authoring sandbox).
Run `alembic upgrade head && pytest`. Requires Slices 1-3. **This slice changes core checkout pricing, so run the
whole existing suite, not only the new files.**

## Promotions and pricing (3.3)
| Requirement | Implementation |
|---|---|
| product / category / cart promotions | Phase 1 `target_type` PRODUCT / CATEGORY / ALL. **ALL = cart promotion**, refined by `min_cart_value` |
| customer-segment promotions | `customer_segment_id`; new `customer_segments` + members (`/v2/segments`) |
| validity, usage limits | unchanged (`starts_at`, `expires_at`, `usage_limit`); `used_count` now counts **once per order** (was once per line) |
| per-customer limits | `per_customer_limit` on promotions and coupons, counted from redemption records |
| priority + stacking | `priority`; `stackable=false` (default) = exclusive. Highest-priority exclusive promotion vs. the stack of all stackable ones: the customer gets the larger discount (ties: exclusive) |
| minimum cart value | `min_cart_value` (compared with the pre-discount cart subtotal, inclusive) |
| maximum discount | promotions: cap across the whole cart, spread proportionally; coupons: cap on the coupon discount |
| coupon eligibility | `GET /v2/pricing/coupon-eligibility` lists every reason (inactive, window, usage limit, segment, per-customer limit, minimum subtotal) and the estimated discount |
| coupon vs promotion | `allow_with_coupon=false` makes a promotion skip carts that use a coupon |
| redemption records | `promotion_redemptions` (APPLIED / REVERSED); reversed, with usage given back, when the whole order is cancelled |
| final price breakdown | `GET /v2/pricing/breakdown`: per line unit price, each promotion, coupon share, loyalty share, tax, line total; totals, loyalty preview, estimated points |

Admin endpoints: `PATCH /v2/promotions/{id}/rules`, `GET /v2/promotions`, `GET /v2/promotions/{id}/redemptions`,
`PATCH /v2/coupons/{id}/rules`, `GET /v2/coupons/{id}/redemptions` (SUPER_ADMIN, ADMIN, PRODUCT_MANAGER);
`/v2/segments` (write: SUPER_ADMIN, ADMIN; read: + PRODUCT_MANAGER, SUPPORT_AGENT).

**Behaviour changes to Phase 1 pricing** (existing data keeps working: new columns default to "exclusive, no limits"):
1. Among exclusive promotions, **priority now decides first**, then the larger discount. Phase 1 ignored priority when picking.
2. Promotion `used_count` is incremented once per order, not once per cart line.
3. `price_cart` has new optional arguments `user_id` and `redeem_points`; legacy calls are unchanged.

## Loyalty (3.4)
* **Ledger** `loyalty_ledger` is append-only and signed; balance = sum. Positive entries are *lots* with `points_remaining` and
  an expiry; redemption spends the lot that expires first (FIFO); expiry writes an `EXPIRE` entry. Every state change has an
  idempotency key (`earn:{order}`, `redeem:{order}`, `expire:{lot}`, ...), so replays never double-credit.
* **Earning rules** `/v2/loyalty/rules`: `earn_rate` points per 1.00 of eligible spend (price x kept units minus discounts, before tax and
  shipping), `min_order_value`, `max_points_per_order`, category, segment, priority, validity window, `expiry_days` (null = default
  365, 0 = never). The highest-priority matching rule is used.
* **Crediting**: once, when a **paid** order becomes **DELIVERED** (v1 status endpoint, v2 shipments, or `POST /v2/loyalty/orders/{id}/credit`).
* **Redeeming at checkout**: `redeem_points` on `POST /checkout` (preview on the breakdown). Value `LOYALTY_POINT_VALUE` (0.25 per point), minimum
  `LOYALTY_MIN_REDEEM_POINTS` (50), at most `LOYALTY_MAX_REDEEM_PERCENT` (50 %) of the goods total (excluding shipping). The discount is spread
  over the lines, so item refunds stay proportional to what was paid. Points are re-checked under row locks; a failure rolls the whole checkout back.
* **Reversal**: whole-order cancellation (v1 or item-level) returns spent points; refunds take back earned points in proportion to the refunded
  share of the payment (only points still unspent; a shortfall is recorded in the entry details, never turned into a negative balance).
* **Expiry**: lazy per customer (balance/redeem calls), `POST /v2/loyalty/expire` (admin), and the scheduler tick.
* Customer: `GET /v2/loyalty/me`, `/me/transactions`. Staff read: SUPER_ADMIN, ADMIN, SUPPORT_AGENT, FINANCE_MANAGER. Adjustments (audited, idempotent
  with `Idempotency-Key`) and rule changes: SUPER_ADMIN, ADMIN. Rule read: + FINANCE_MANAGER.

## Changed Phase 1 / earlier-slice code
`services/pricing.py` (rewritten from `_load_coupon` down), `api/checkout.py` (price_cart args, loyalty columns, redemptions, points),
`api/cart_pricing.py` (passes `user_id`), `api/order_cancellation.py` and `services/order_fulfilment.py` (benefit reversal, delivery crediting),
`api/order_status.py` (delivery crediting), `services/payments.py` (point reversal on refund), `services/jobs.py`, `schemas/order.py`
(`redeem_points`), models `promotion.py`, `coupon.py`, `order.py` (new columns), `core/config.py`.

## Known limits
* Not executed. Pricing changes are the riskiest part: please run the full suite and compare a few real carts before/after.
* Partial item cancellation does not return a share of spent points or promotion usage; only a full cancellation does.
* Segments are manual membership for now (rule-based groups, tags, notes and restrictions come with module 3.5).
* Refunds after a loyalty redemption are capped by what was actually paid (grand total), so the points value is never refunded in cash.
* Order status DELIVERED is the "completed" trigger; there is no separate return-window delay before points are credited.
* Postman collection not extended yet.
