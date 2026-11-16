# Phase 2 – Slice 2: Advanced Order Management (module 3.1)

**Status: written and syntax/name-checked only. NOT executed** (no network in the authoring sandbox).
Run `alembic upgrade head && pytest tests/test_phase2_orders.py` first. Requires Slice 1.

## Model
`order_items` gains unit counters: `quantity_shipped / delivered / cancelled / backordered`.
New tables: `order_shipments`, `order_shipment_items`, `order_backorders`, `order_item_cancellations`.
Order status is **derived** from the counters (`recompute_order_status`) and applied through one
transition map, so every change writes `order_status_history` (actor, timestamp, reason in `note`).

```
PLACED/PENDING -> CONFIRMED -> PROCESSING -> PARTIALLY_SHIPPED -> SHIPPED -> DELIVERED
                       \            \-> BACKORDERED <-> PROCESSING
                        \-> CANCELLED (from any pre-shipment state)
```

## Endpoints (`/v2/orders`)
| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/{id}/fulfilment` | customer (own), all staff except seller | counters, shipments, back-orders |
| GET | `/{id}/timeline` | same | status changes, shipments, back-orders, cancellations; actor ids hidden from customers |
| POST | `/{id}/shipments` | SUPER_ADMIN, ADMIN, ORDER_MANAGER, DELIVERY_MANAGER | split shipment; `Idempotency-Key` supported |
| POST | `/{id}/shipments/{sid}/deliver` | same | 409 if already delivered |
| POST | `/{id}/items/{iid}/backorder` | SUPER_ADMIN, ADMIN, ORDER_MANAGER, INVENTORY_MANAGER | |
| POST | `/{id}/backorders/{bid}/resolve` | same | |
| POST | `/{id}/cancel-items` | customer (own, before PROCESSING), ORDER_MANAGER/ADMIN/SUPER_ADMIN (until shipped) | restores stock, proportional refund; `Idempotency-Key` supported |
| POST | `/{id}/status` | ORDER_MANAGER/ADMIN/SUPER_ADMIN | manual: only ->CONFIRMED, ->PROCESSING; reason required |
| PATCH | `/{id}` | same | shipping fields/notes, only before anything ships; audited (field names only) |

## Rules
* Ship/back-order quantity <= `quantity - cancelled - shipped - backordered`.
* Cancel quantity <= `quantity - cancelled - shipped`; free units are used first, then open back-orders shrink.
* Refund for a cancelled line = `line_total * qty / ordered` (telescoping, no rounding drift). When the last
  unit is cancelled the refund is the whole remaining paid amount (shipping/tax included).
* Gateway refund runs after commit; on gateway failure the cancellation stands and the refund stays APPROVED for retry.
* Partial cancellation of an **unpaid** order is rejected (409): the pending payment session would still charge the old total.

## Changed Phase 1 code
* `api/order_cancellation.py`: restores only still-outstanding units (prevents double stock restore after item
  cancellations), closes open back-orders, BACKORDERED orders are staff-cancellable.
* `api/order_status.py`: after SHIPPED/DELIVERED, item counters are synced so v1 and v2 agree.
* `models/order.py`, `models/__init__.py`, `main.py`, `services/idempotency.py` (added `start()`).

## Known limits
* Not executed; row locking and the MySQL-only backfill in migration `c3d5e7f9a1b2` (UPDATE ... JOIN) are untested.
* Phase 1 item endpoint `PATCH /orders/{id}/items/{iid}/fulfillment` still changes item status without touching
  the counters; use the v2 shipment endpoints for split fulfilment.
* Back-orders do not change inventory (Phase 1 already deducted stock at checkout).
* Shipping requires CONFIRMED+ status but not payment status (cash-on-delivery stays possible).
* Postman collection not yet extended; Swagger text is in the route summaries/responses.
