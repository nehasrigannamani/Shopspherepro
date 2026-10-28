"""Generate the Postman collection and environment.

    python -m scripts.generate_postman

* "Core Workflow" and "Error Handling" are hand-written requests with test
  scripts (status codes + variable chaining).
* "All Endpoints" is generated from the routers (every route in the app).
Run it again after adding endpoints to refresh the last folder.
"""
import ast
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
API_DIR = ROOT / "app" / "api"
OUT = ROOT / "postman"
METHODS = {"get", "post", "put", "patch", "delete"}


def url(path: str) -> dict:
    path = re.sub(r"(?<!\{)\{(\w+)\}(?!\})", r"{{\1}}", path)
    parts = [p for p in path.split("/") if p]
    return {"raw": "{{base_url}}" + (path or "/"), "host": ["{{base_url}}"], "path": parts}


def req(name, method, path, token=None, body=None, tests=None, query=None, raw_headers=None, description=None):
    headers = [{"key": "Content-Type", "value": "application/json"}] if body is not None else []
    headers += raw_headers or []
    request = {"method": method, "header": headers, "url": url(path)}
    if query:
        request["url"]["query"] = [{"key": k, "value": v} for k, v in query.items()]
        request["url"]["raw"] += "?" + "&".join(f"{k}={v}" for k, v in query.items())
    if token:
        request["auth"] = {"type": "bearer", "bearer": [{"key": "token", "value": "{{%s}}" % token, "type": "string"}]}
    else:
        request["auth"] = {"type": "noauth"}
    if body is not None:
        request["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2), "options": {"raw": {"language": "json"}}}
    if description:
        request["description"] = description
    item = {"name": name, "request": request}
    if tests:
        item["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": tests}}]
    return item


def status_test(code, *extra):
    lines = [f'pm.test("status is {code}", function () {{ pm.response.to.have.status({code}); }});']
    lines += list(extra)
    return lines


def save(var, expr):
    return f'pm.collectionVariables.set("{var}", {expr});'


def login(name, email_var, token_var, refresh_var=None):
    extra = [save(token_var, "pm.response.json().access_token")]
    if refresh_var:
        extra.append(save(refresh_var, "pm.response.json().refresh_token"))
    return req(name, "POST", "/auth/login", body={"email": "{{%s}}" % email_var, "password": "{{password}}"},
               tests=status_test(200, 'pm.test("has refresh token", function () { pm.expect(pm.response.json().refresh_token).to.be.a("string"); });', *extra))


def core_workflow():
    return [
        {"name": "1. Authentication", "item": [
            req("Register customer", "POST", "/auth/register",
                body={"full_name": "Postman Customer", "email": "{{new_customer_email}}", "phone": "+919876500001", "password": "Passw0rd!2026"},
                tests=status_test(201, 'pm.test("role is CUSTOMER", function () { pm.expect(pm.response.json().role).to.eql("CUSTOMER"); });')),
            login("Login customer (seeded)", "customer_email", "customer_token", "customer_refresh"),
            req("Refresh tokens", "POST", "/auth/refresh", body={"refresh_token": "{{customer_refresh}}"},
                tests=status_test(200, save("customer_token", "pm.response.json().access_token"), save("customer_refresh", "pm.response.json().refresh_token"))),
            req("Who am I", "GET", "/auth/me", token="customer_token", tests=status_test(200)),
            login("Login admin", "admin_email", "admin_token"),
            login("Login finance manager", "finance_email", "finance_token"),
            login("Login order manager", "order_manager_email", "order_manager_token"),
            req("Verify email (token from email/log)", "POST", "/auth/verify-email", body={"token": "paste-token-from-email"},
                description="Run 'Register customer' first, then copy the token from the console/email."),
        ]},
        {"name": "2. Profile and addresses", "item": [
            req("Get profile", "GET", "/profile", token="customer_token", tests=status_test(200)),
            req("Update profile", "PATCH", "/profile", token="customer_token", body={"full_name": "Chris C. Customer"}, tests=status_test(200)),
            req("Profile change history", "GET", "/profile/history", token="customer_token", tests=status_test(200)),
            req("Add address", "POST", "/addresses", token="customer_token",
                body={"address_type": "BOTH", "label": "Work", "full_name": "Chris Customer", "phone": "+919000000001",
                      "line1": "7 Residency Road", "city": "Bengaluru", "state": "Karnataka", "postal_code": "560025", "country": "IN", "is_default": True},
                tests=status_test(201, save("address_id", "pm.response.json().id"))),
            req("List addresses", "GET", "/addresses", token="customer_token", tests=status_test(200)),
        ]},
        {"name": "3. Catalog (public data via seed)", "item": [
            req("List products", "GET", "/products", token="customer_token", tests=status_test(200)),
            req("Get product", "GET", "/products/{{product_id}}", token="customer_token", tests=status_test(200)),
            req("Inventory movements for product", "GET", "/inventory/{{product_id}}/movements", token="order_manager_token", tests=status_test(403), description="Order managers are not inventory staff: 403 demonstrates role enforcement."),
        ]},
        {"name": "4. Cart and checkout", "item": [
            req("Add item to cart", "POST", "/cart/items", token="customer_token", body={"product_id": "{{product_id}}", "quantity": 2},
                tests=status_test(200, 'pm.test("cart has items", function () { pm.expect(pm.response.json().total_items).to.be.above(0); });')),
            req("Cart price summary (tax, shipping, coupon)", "GET", "/cart/summary", token="customer_token",
                query={"shipping_method_id": "{{shipping_method_id}}", "coupon_code": "WELCOME10", "state": "Karnataka"},
                tests=status_test(200, save("expected_total", "pm.response.json().grand_total"))),
            req("Checkout (saved address)", "POST", "/checkout", token="customer_token",
                body={"shipping_method_id": "{{shipping_method_id}}", "shipping_address_id": "{{address_id}}", "coupon_code": "WELCOME10"},
                tests=status_test(201, save("order_id", "pm.response.json().id"),
                                  'pm.test("server total equals preview", function () { pm.expect(pm.response.json().grand_total).to.eql(pm.collectionVariables.get("expected_total")); });')),
            req("Duplicate checkout is rejected", "POST", "/checkout", token="customer_token",
                body={"shipping_method_id": "{{shipping_method_id}}", "shipping_address_id": "{{address_id}}"}, tests=status_test(409)),
            req("Get order", "GET", "/orders/{{order_id}}", token="customer_token", tests=status_test(200)),
            req("Order items", "GET", "/orders/{{order_id}}/items", token="customer_token", tests=status_test(200)),
            req("Search my orders", "GET", "/orders", token="customer_token", query={"status": "PLACED"}, tests=status_test(200)),
        ]},
        {"name": "5. Payment (Stripe test mode)", "item": [
            req("Create checkout session", "POST", "/payments/orders/{{order_id}}/checkout-session", token="customer_token",
                tests=status_test(201, save("payment_id", "pm.response.json().id"), save("checkout_url", "pm.response.json().checkout_url")),
                description="Open checkout_url in a browser and pay with test card 4242 4242 4242 4242."),
            req("Create session again (returns same payment)", "POST", "/payments/orders/{{order_id}}/checkout-session", token="customer_token", tests=status_test(201)),
            req("Sync payment from Stripe (if webhook not running)", "POST", "/payments/{{payment_id}}/sync", token="customer_token", tests=status_test(200)),
            req("Payment transaction history", "GET", "/payments/{{payment_id}}/transactions", token="customer_token", tests=status_test(200)),
            req("Webhook (signature required - use `stripe listen`)", "POST", "/payments/webhook", body={},
                tests=status_test(400), description="Without a valid Stripe-Signature the API correctly answers 400."),
        ]},
        {"name": "6. Fulfilment", "item": [
            req("Confirm order (if COD/manual)", "PATCH", "/orders/{{order_id}}/status", token="order_manager_token", body={"status": "CONFIRMED", "note": "verified"}),
            req("Invalid transition is rejected", "PATCH", "/orders/{{order_id}}/status", token="order_manager_token", body={"status": "DELIVERED"}, tests=status_test(409)),
            req("Status history", "GET", "/orders/{{order_id}}/status-history", token="customer_token", tests=status_test(200)),
        ]},
        {"name": "7. Refund", "item": [
            req("Request partial refund", "POST", "/refunds", token="customer_token", body={"payment_id": "{{payment_id}}", "amount": "100.00", "reason": "Item damaged"},
                tests=status_test(201, save("refund_id", "pm.response.json().id"))),
            req("Approve refund (finance)", "PATCH", "/refunds/{{refund_id}}/decision", token="finance_token", body={"status": "APPROVED", "decision_note": "ok"}, tests=status_test(200)),
            req("Process refund via Stripe", "POST", "/refunds/{{refund_id}}/process", token="finance_token", tests=status_test(200)),
            req("Over-refund is rejected", "POST", "/refunds", token="customer_token", body={"payment_id": "{{payment_id}}", "amount": "999999.00", "reason": "too much"}, tests=status_test(400)),
        ]},
        {"name": "8. Cancellation", "item": [
            req("Cancel order", "POST", "/orders/{{order_id}}/cancel", token="customer_token", body={"reason": "Changed my mind"}),
        ]},
        {"name": "9. Staff: users, reports, exports, jobs", "item": [
            req("List users", "GET", "/users", token="admin_token", query={"role": "CUSTOMER"}, tests=status_test(200)),
            req("Role permission matrix", "GET", "/users/roles/permissions", token="admin_token", tests=status_test(200)),
            req("Dashboard overview", "GET", "/dashboard/overview", token="admin_token", tests=status_test(200)),
            req("Sales report", "GET", "/reports/sales", token="finance_token", query={"date_from": "2026-01-01"}, tests=status_test(200)),
            req("Inventory report by warehouse", "GET", "/reports/inventory", token="admin_token", query={"warehouse_id": "1"}, tests=status_test(200)),
            req("Export sales as Excel", "GET", "/report-exports/sales.xlsx", token="finance_token", tests=status_test(200)),
            req("Export inventory as PDF", "GET", "/report-exports/inventory.pdf", token="admin_token", tests=status_test(200)),
            req("Create background export job", "POST", "/report-exports/jobs", token="admin_token", body={"report": "orders", "format": "csv"},
                tests=status_test(202, save("job_id", "pm.response.json().id"))),
            req("Job status", "GET", "/report-exports/jobs/{{job_id}}", token="admin_token", tests=status_test(200)),
            req("Schedule daily inventory summary", "POST", "/report-exports/schedules", token="admin_token", body={"report": "inventory", "format": "xlsx", "frequency": "DAILY"}, tests=status_test(201)),
            req("Run scheduler now", "POST", "/report-exports/scheduler/run", token="admin_token", tests=status_test(200)),
            req("Dispatch queued emails/SMS", "POST", "/notification-deliveries/dispatch", token="admin_token", tests=status_test(200)),
            req("Audit log", "GET", "/audit-logs", token="admin_token", query={"limit": "20"}, tests=status_test(200)),
        ]},
        {"name": "10. Logout", "item": [
            req("Logout (revokes session)", "POST", "/auth/logout", token="customer_token", tests=status_test(204)),
            req("Token no longer works", "GET", "/auth/me", token="customer_token", tests=status_test(401)),
        ]},
    ]


def error_handling():
    return [
        req("401 - missing token", "GET", "/orders", tests=['pm.test("401 or 403", function () { pm.expect([401, 403]).to.include(pm.response.code); });']),
        req("401 - bad token", "GET", "/auth/me", raw_headers=[{"key": "Authorization", "value": "Bearer not-a-token"}], tests=status_test(401)),
        req("401 - wrong password", "POST", "/auth/login", body={"email": "{{customer_email}}", "password": "wrong"}, tests=status_test(401)),
        req("403 - customer calling admin API", "GET", "/users", token="customer_token", tests=status_test(403)),
        req("403 - finance cannot read inventory report", "GET", "/reports/inventory", token="finance_token", tests=status_test(403)),
        req("404 - unknown order", "GET", "/orders/99999999", token="customer_token", tests=status_test(404)),
        req("409 - register duplicate email", "POST", "/auth/register", body={"full_name": "Dup", "email": "{{customer_email}}", "password": "Passw0rd!2026"}, tests=status_test(409)),
        req("422 - invalid phone", "POST", "/auth/register", body={"full_name": "Bad Phone", "email": "bad@example.com", "phone": "123", "password": "Passw0rd!2026"}, tests=status_test(422)),
        req("422 - checkout without address", "POST", "/checkout", token="customer_token", body={"shipping_method_id": 1}, tests=status_test(422)),
    ]


def scan_routes():
    folders: dict[str, list] = {}
    for path in sorted(API_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        prefix, tags = "", [path.stem.replace("_", " ").title()]
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "APIRouter":
                for kw in node.keywords:
                    if kw.arg == "prefix":
                        prefix = ast.literal_eval(kw.value)
                    if kw.arg == "tags":
                        tags = ast.literal_eval(kw.value)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                        and deco.func.attr in METHODS and deco.args):
                    route = ast.literal_eval(deco.args[0])
                    method = deco.func.attr.upper()
                    full = prefix + route
                    body = {} if method in {"POST", "PUT", "PATCH"} else None
                    folders.setdefault(tags[0], []).append(
                        req(f"{method} {full or '/'}", method, full or "/", token="admin_token", body=body,
                            description=(ast.get_docstring(node) or "").strip() or None))
    return [{"name": name, "item": items} for name, items in sorted(folders.items())]


def main():
    OUT.mkdir(exist_ok=True)
    collection = {
        "info": {
            "name": "ShopSphere Pro API",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "description": "Run the folders in order. Seed data first: python -m scripts.seed",
        },
        "variable": [
            {"key": "base_url", "value": "http://localhost:8000"},
            {"key": "password", "value": "ShopSphere#2026"},
            {"key": "customer_email", "value": "customer@shopsphere.example.com"},
            {"key": "admin_email", "value": "admin@shopsphere.example.com"},
            {"key": "finance_email", "value": "finance.manager@shopsphere.example.com"},
            {"key": "order_manager_email", "value": "order.manager@shopsphere.example.com"},
            {"key": "new_customer_email", "value": "postman.customer@example.com"},
            {"key": "product_id", "value": "1"},
            {"key": "shipping_method_id", "value": "1"},
        ],
        "item": [
            {"name": "Core Workflow", "item": core_workflow()},
            {"name": "Error Handling", "item": error_handling()},
            {"name": "All Endpoints", "item": scan_routes()},
        ],
    }
    (OUT / "ShopSphere_Pro.postman_collection.json").write_text(json.dumps(collection, indent=2))
    environment = {
        "name": "ShopSphere Local",
        "values": [{"key": v["key"], "value": v["value"], "enabled": True} for v in collection["variable"]],
        "_postman_variable_scope": "environment",
    }
    (OUT / "ShopSphere_Local.postman_environment.json").write_text(json.dumps(environment, indent=2))
    total = sum(len(f["item"]) for f in collection["item"][2]["item"])
    print(f"Wrote collection ({total} generated endpoint requests).")


if __name__ == "__main__":
    main()
