# Module coverage

"route + schema only" means the endpoints existed in the originally supplied code and were not changed or newly
tested in this revision; they have not been re-verified here.

| # | Module | Code | Endpoints | Test coverage |
|---|---|---|---|---|
| 1 | Authentication & authorization | auth, dependencies | /auth/login, /refresh, /logout, /logout-all, /change-password | test_auth, test_rbac_and_users |
| 2 | User management | users | /users (CRUD, search/filter, role, activate/deactivate, activity, permission matrix) | test_rbac_and_users |
| 3 | Customer registration | auth | /auth/register, /verify-email, /resend-verification | test_auth |
| 4 | Customer profile | profile | /profile, /profile/history, /profile/deactivation-requests | test_accounts |
| 5 | Address management | addresses, checkout | /addresses (+ default, billing/shipping); checkout uses address ids | test_accounts |
| 6 | Categories | categories | /categories | route + schema only |
| 7 | Brands | brands | /brands | route + schema only |
| 8 | Products | products | /products | route + schema only |
| 9 | Variants | product_variants, variant_inventory, cart, checkout | /products/{id}/variants, /variant-inventory; variants purchasable via cart/checkout | test_shopping_workflow |
| 10 | Images & documents | product_media | /products/{id}/media | route + schema only |
| 11 | Attributes | product_attributes | /product-attributes | route + schema only |
| 12 | Pricing | product_pricing | /products/{id}/price, /price-history | route + schema only |
| 13 | Inventory | inventory | /inventory | stock movements covered via checkout/cancel tests |
| 14 | Warehouses | warehouses | /warehouses | route + schema only |
| 15 | Transfers | inventory_transfers | /inventory-transfers | route + schema only |
| 16 | Low stock | low_stock, checkout | /low-stock; alerts to inventory managers from checkout | test_shopping_workflow |
| 17 | Cart | cart | /cart (variant-aware) | test_shopping_workflow |
| 18 | Cart pricing | cart_pricing, services/pricing | /cart/summary (discount, shipping, tax) | test_shopping_workflow, test_units |
| 19 | Wishlist | wishlist | /wishlist | route + schema only |
| 20 | Coupons | coupons, pricing | /coupons | test_units (rules), test_shopping_workflow |
| 21 | Promotions | promotions, pricing | /promotions | applied in pricing |
| 22 | Tax | taxes, pricing | /taxes | test_units |
| 23 | Shipping config | shipping | /shipping | used in checkout tests |
| 24 | Checkout | checkout | /checkout | test_shopping_workflow |
| 25 | Orders | orders | /orders (search/filter), /orders/admin/all | test_shopping_workflow |
| 26 | Order items | order_items | /orders/{id}/items, item fulfillment status, variant/SKU snapshots | test_shopping_workflow |
| 27 | Order status | order_status | /orders/{id}/status, /status-history | test_shopping_workflow |
| 28 | Cancellation | order_cancellation | /orders/{id}/cancel (stock release + refund) | test_shopping_workflow |
| 29 | Payments | payments, services/payments | /payments (session, webhook, sync, list) | test_shopping_workflow |
| 30 | Payment history | payment_transactions | /payments/{id}/transactions | test_shopping_workflow |
| 31 | Refunds | refunds | /refunds (+ /process via Stripe) | test_shopping_workflow |
| 32 | Cash on delivery | cash_on_delivery | /cod | route + schema only |
| 33 | Shipping orders | shipping_orders | /shipping/orders | route + schema only |
| 34 | Tracking | shipment_tracking | /shipping/tracking | route + schema only |
| 35 | Deliveries | deliveries | /deliveries | route + schema only |
| 36 | Returns | returns | /returns | dashboard/report only |
| 37 | Exchanges | exchanges | /exchanges | route + schema only |
| 38 | Reviews | product_reviews | /reviews | route + schema only |
| 39 | Q&A | product_questions | /questions | route + schema only |
| 40 | Support tickets | support_tickets | /support/tickets | route + schema only |
| 41 | Notifications | notifications, services/notify | /notifications; event hooks in checkout, payments, refunds, status, cancellation | test_reports_and_jobs |
| 42 | Email & SMS | notification_deliveries, services/delivery | /notification-deliveries (+ /dispatch, /retry) | test_reports_and_jobs |
| 43 | Suppliers/sellers | suppliers | /suppliers | route + schema only |
| 44 | Purchase orders | purchase_orders | /purchase-orders | report only |
| 45 | Supplier invoices | supplier_invoices | /supplier-invoices | schema tests |
| 46 | Audit logs | audit_logs, core/middleware | /audit-logs; automatic logging; append-only trigger | test_rbac_and_users |
| 47 | Dashboard | dashboard | /dashboard/overview, /dashboard/{section} | test_reports_and_jobs |
| 48 | Reports | reports, services/reports | /reports/{sales,orders,inventory,low-stock,payments,refunds,returns,product-performance,category-performance,purchase-orders,supplier-invoices} | test_reports_and_jobs |
| 49 | Exports & background tasks | report_exports, services/jobs | CSV/XLSX/PDF, /jobs, /schedules, /scheduler/run | test_reports_and_jobs |
| 50 | Testing & integration | tests/, postman/ | pytest suite + Postman collection | this repo |
