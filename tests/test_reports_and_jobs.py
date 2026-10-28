"""Dashboard, reports, exports, background jobs and notification delivery."""
import os
from datetime import datetime, timedelta
from decimal import Decimal

from app.models import (
    BackgroundJob, NotificationDelivery, ReportSchedule, UserRole,
)
from app.services.jobs import run_job

from tests.conftest import bearer, login, pay_order, place_order

CUSTOMER = UserRole.CUSTOMER
FINANCE = UserRole.FINANCE_MANAGER
INVENTORY = UserRole.INVENTORY_MANAGER
ADMIN = UserRole.ADMIN


def paid_order(client, h, catalog):
    order = place_order(client, h(CUSTOMER), catalog.product_id, catalog.shipping_id)
    pay_order(client, h(CUSTOMER), order)
    return order


# --------------------------------------------------------------- dashboard ---

def test_dashboard_sections_depend_on_role(client, h, catalog, stripe_mock):
    paid_order(client, h, catalog)
    finance = client.get("/dashboard/overview", headers=h(FINANCE)).json()
    assert set(finance) == {"orders_by_status", "revenue", "payments_by_status"}
    assert Decimal(finance["revenue"]["gross_revenue"]) == Decimal("1230")
    assert finance["orders_by_status"] == {"CONFIRMED": 1}
    assert finance["payments_by_status"]["SUCCEEDED"]["count"] == 1

    inventory = client.get("/dashboard/overview", headers=h(INVENTORY)).json()
    assert set(inventory) == {"products_inventory", "low_stock"}
    assert inventory["products_inventory"]["units_on_hand"] == 8

    admin = client.get("/dashboard/overview", headers=h(ADMIN)).json()
    assert set(admin) == {"products_inventory", "orders_by_status", "revenue", "payments_by_status", "low_stock", "returns"}


def test_dashboard_section_access_is_enforced(client, h):
    assert client.get("/dashboard/revenue", headers=h(INVENTORY)).status_code == 403
    assert client.get("/dashboard/low_stock", headers=h(INVENTORY)).status_code == 200
    assert client.get("/dashboard/nope", headers=h(ADMIN)).status_code == 404
    assert client.get("/dashboard/summary", headers=h(ADMIN)).status_code == 200


def test_refunds_reduce_net_revenue(client, h, catalog, stripe_mock):
    order = paid_order(client, h, catalog)
    payment_id = client.get("/payments", headers=h(CUSTOMER)).json()[0]["id"]
    refund = client.post("/refunds", json={"payment_id": payment_id, "amount": "230.00", "reason": "partial"}, headers=h(CUSTOMER)).json()
    client.patch(f"/refunds/{refund['id']}/decision", json={"status": "APPROVED"}, headers=h(FINANCE))
    client.post(f"/refunds/{refund['id']}/process", headers=h(FINANCE))
    revenue = client.get("/dashboard/revenue", headers=h(FINANCE)).json()
    assert Decimal(revenue["net_revenue"]) == Decimal("1000")


# ----------------------------------------------------------------- reports ---

def test_report_catalog_lists_only_permitted_reports(client, h):
    finance = client.get("/reports/catalog", headers=h(FINANCE)).json()["reports"]
    assert "payments" in finance and "inventory" not in finance
    inventory = client.get("/reports/catalog", headers=h(INVENTORY)).json()["reports"]
    assert "inventory" in inventory and "payments" not in inventory


def test_sales_report_counts_only_paid_orders(client, h, catalog, stripe_mock):
    paid_order(client, h, catalog)
    client.post("/cart/items", json={"product_id": catalog.product_id, "quantity": 1}, headers=h(CUSTOMER))
    from tests.conftest import checkout_payload
    client.post("/checkout", json=checkout_payload(catalog.shipping_id), headers=h(CUSTOMER))  # unpaid

    report = client.get("/reports/sales", headers=h(FINANCE)).json()
    assert report["summary"]["orders"] == 1
    assert Decimal(report["summary"]["revenue"]) == Decimal("1230")
    assert report["columns"][0] == "Date"


def test_order_report_filters(client, h, catalog, stripe_mock):
    paid_order(client, h, catalog)
    assert client.get("/reports/orders", params={"status": "CONFIRMED"}, headers=h(FINANCE)).json()["row_count"] == 1
    assert client.get("/reports/orders", params={"status": "SHIPPED"}, headers=h(FINANCE)).json()["row_count"] == 0
    assert client.get("/reports/orders", params={"date_from": "2999-01-01"}, headers=h(FINANCE)).json()["row_count"] == 0
    assert client.get("/reports/orders", params={"date_from": "2030-02-01", "date_to": "2030-01-01"}, headers=h(FINANCE)).status_code == 400


def test_inventory_report_with_category_and_low_stock(client, h, catalog):
    report = client.get("/reports/inventory", params={"category_id": catalog.category_id}, headers=h(INVENTORY)).json()
    assert report["row_count"] == 1 and report["rows"][0][0] == "P-1"
    assert client.get("/reports/inventory", params={"category_id": 999}, headers=h(INVENTORY)).json()["row_count"] == 0
    assert client.get("/reports/low-stock", headers=h(INVENTORY)).json()["row_count"] == 0


def test_product_and_category_performance(client, h, catalog, stripe_mock):
    paid_order(client, h, catalog)
    product = client.get("/reports/product-performance", headers=h(FINANCE)).json()
    assert product["rows"][0][:3] == ["P-1", "Widget", 2]
    category = client.get("/reports/category-performance", headers=h(FINANCE)).json()
    assert category["rows"][0][0] == "Electronics"


def test_payment_and_refund_reports_are_finance_only(client, h, catalog, stripe_mock):
    paid_order(client, h, catalog)
    assert client.get("/reports/payments", headers=h(FINANCE)).json()["row_count"] == 1
    assert client.get("/reports/payments", headers=h(INVENTORY)).status_code == 403
    assert client.get("/reports/refunds", headers=h(UserRole.ORDER_MANAGER)).status_code == 403
    assert client.get("/reports/does-not-exist", headers=h(ADMIN)).status_code == 404


def test_legacy_purchase_order_reports_still_work(client, h):
    for path in ("/reports/purchase-orders", "/reports/supplier-invoices", "/reports/summary"):
        assert client.get(path, headers=h(FINANCE)).status_code == 200, path


# ----------------------------------------------------------------- exports ---

def test_direct_exports_in_all_three_formats(client, h, catalog):
    headers = h(INVENTORY)
    csv_response = client.get("/report-exports/inventory.csv", headers=headers)
    assert csv_response.status_code == 200 and csv_response.headers["content-type"].startswith("text/csv")
    assert csv_response.text.splitlines()[0].startswith("SKU")
    assert "P-1" in csv_response.text

    xlsx = client.get("/report-exports/inventory.xlsx", headers=headers)
    assert xlsx.content[:2] == b"PK" and "spreadsheetml" in xlsx.headers["content-type"]

    pdf = client.get("/report-exports/inventory.pdf", headers=headers)
    assert pdf.content[:4] == b"%PDF" and pdf.headers["content-type"] == "application/pdf"


def test_export_access_and_unknown_inputs(client, h):
    assert client.get("/report-exports/inventory.csv", headers=h(FINANCE)).status_code == 403
    assert client.get("/report-exports/nonsense.csv", headers=h(ADMIN)).status_code == 404
    assert client.get("/report-exports/inventory.txt", headers=h(ADMIN)).status_code == 404
    assert client.get("/report-exports/purchase-orders.csv", headers=h(FINANCE)).status_code == 200


def test_background_export_job_produces_downloadable_file(client, h, catalog):
    headers = h(INVENTORY)
    created = client.post("/report-exports/jobs", json={"report": "inventory", "format": "xlsx"}, headers=headers)
    assert created.status_code == 202
    job_id = created.json()["id"]

    job = client.get(f"/report-exports/jobs/{job_id}", headers=headers).json()
    assert job["status"] == "SUCCEEDED" and job["downloadable"] is True and job["attempts"] == 1
    download = client.get(f"/report-exports/jobs/{job_id}/download", headers=headers)
    assert download.status_code == 200 and download.content[:2] == b"PK"


def test_jobs_are_private_to_their_creator(client, h, catalog):
    job_id = client.post("/report-exports/jobs", json={"report": "inventory", "format": "csv"}, headers=h(INVENTORY)).json()["id"]
    assert client.get(f"/report-exports/jobs/{job_id}", headers=h(FINANCE)).status_code == 404
    assert client.get(f"/report-exports/jobs/{job_id}", headers=h(ADMIN)).status_code == 200
    assert client.post("/report-exports/jobs", json={"report": "inventory"}, headers=h(FINANCE)).status_code == 403


def test_failed_job_is_recorded_and_retries_are_bounded(client, db, h, users):
    job = BackgroundJob(job_type="REPORT_EXPORT", params={"report": "does-not-exist"},
                        max_attempts=2, created_by=users[ADMIN].id)
    db.add(job)
    db.commit()
    run_job(job.id)

    db.expire_all()
    job = db.get(BackgroundJob, job.id)
    assert job.status == "FAILED" and job.attempts == 1 and "KeyError" in job.error_message

    admin = h(ADMIN)
    assert client.post(f"/report-exports/jobs/{job.id}/retry", headers=admin).status_code == 202
    db.expire_all()
    assert db.get(BackgroundJob, job.id).attempts == 2
    assert client.post(f"/report-exports/jobs/{job.id}/retry", headers=admin).status_code == 409


def test_succeeded_job_cannot_be_retried(client, h, catalog):
    headers = h(INVENTORY)
    job_id = client.post("/report-exports/jobs", json={"report": "inventory"}, headers=headers).json()["id"]
    assert client.post(f"/report-exports/jobs/{job_id}/retry", headers=headers).status_code == 409


def test_schedule_creates_job_when_due(client, db, h, catalog):
    created = client.post("/report-exports/schedules", json={"report": "inventory", "format": "csv", "frequency": "DAILY"}, headers=h(INVENTORY))
    assert created.status_code == 201
    schedule_id = created.json()["id"]

    # Not due yet: nothing is scheduled.
    assert client.post("/report-exports/scheduler/run", headers=h(ADMIN)).json()["scheduled_jobs"] == []

    schedule = db.get(ReportSchedule, schedule_id)
    schedule.next_run_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()

    result = client.post("/report-exports/scheduler/run", headers=h(ADMIN)).json()
    assert len(result["scheduled_jobs"]) == 1

    db.expire_all()
    job = db.get(BackgroundJob, result["scheduled_jobs"][0])
    assert job.job_type == "SCHEDULED_SUMMARY" and job.status == "SUCCEEDED" and job.schedule_id == schedule_id
    assert os.path.exists(job.result_path)
    assert db.get(ReportSchedule, schedule_id).next_run_at > datetime.utcnow()

    assert client.post("/report-exports/scheduler/run", headers=h(FINANCE)).status_code == 403


def test_deactivated_schedule_does_not_run(client, db, h, catalog):
    schedule_id = client.post("/report-exports/schedules", json={"report": "orders", "frequency": "WEEKLY"}, headers=h(ADMIN)).json()["id"]
    assert client.delete(f"/report-exports/schedules/{schedule_id}", headers=h(ADMIN)).status_code == 204
    db.get(ReportSchedule, schedule_id).next_run_at = datetime.utcnow() - timedelta(days=1)
    db.commit()
    assert client.post("/report-exports/scheduler/run", headers=h(ADMIN)).json()["scheduled_jobs"] == []


# ------------------------------------------------- notification delivery ---

def register(client):
    return client.post("/auth/register", json={
        "full_name": "Mail Test", "email": "mail@example.com", "password": "Sup3rSecret!"})


def test_queued_email_is_sent_by_dispatch(client, db, h):
    register(client)
    db.expire_all()
    assert db.query(NotificationDelivery).one().status == "PENDING"
    result = client.post("/notification-deliveries/dispatch", headers=h(ADMIN)).json()
    assert result["sent"] == 1
    db.expire_all()
    delivery = db.query(NotificationDelivery).one()
    assert delivery.status == "SENT" and delivery.attempts == 1 and delivery.sent_at is not None


def test_notification_job_dispatches_in_background(client, db, h):
    register(client)
    job = client.post("/report-exports/jobs/notifications", headers=h(ADMIN)).json()
    assert client.get(f"/report-exports/jobs/{job['id']}", headers=h(ADMIN)).json()["status"] == "SUCCEEDED"
    db.expire_all()
    assert db.query(NotificationDelivery).one().status == "SENT"


def test_failed_delivery_is_retried_one_attempt_at_a_time(client, db, h, monkeypatch):
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.delenv("EMAIL_HOST", raising=False)
    register(client)
    admin = h(ADMIN)

    client.post("/notification-deliveries/dispatch", headers=admin)
    db.expire_all()
    delivery = db.query(NotificationDelivery).one()
    assert delivery.status == "FAILED" and delivery.attempts == 1  # ONE attempt, not three
    assert "credentials" not in (delivery.error_message or "").lower()

    # Automatic dispatch respects the backoff window...
    again = client.post("/notification-deliveries/dispatch", headers=admin).json()
    assert again["skipped_backoff"] == 1

    # ...while a manual retry is allowed until the cap is reached.
    for expected in (2, 3):
        assert client.post(f"/notification-deliveries/{delivery.id}/retry", headers=admin).status_code == 200
        db.expire_all()
        assert db.query(NotificationDelivery).one().attempts == expected
    assert client.post(f"/notification-deliveries/{delivery.id}/retry", headers=admin).status_code == 400

    # Fix the provider: a fresh delivery then succeeds.
    monkeypatch.setenv("EMAIL_BACKEND", "console")


def test_staff_receive_notifications_for_events(client, db, h, users, catalog, stripe_mock):
    paid_order(client, h, catalog)
    mine = client.get("/notifications/mine", headers=h(CUSTOMER)).json()
    assert {"ORDER", "PAYMENT"} <= {n["notification_type"] for n in mine}
    assert client.get("/notifications/mine", headers=h(FINANCE)).json() == []
