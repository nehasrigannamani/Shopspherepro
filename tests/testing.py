
import json
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.supplier_invoices import (
    SupplierInvoiceCreate,
    SupplierInvoiceStatusUpdate,
)
from app.schemas.audit_logs import AuditLogCreate
from app.services.audit import write_audit_log


client = TestClient(app)


# ==================================================
# 1. APPLICATION AND API SMOKE TESTS
# ==================================================

def test_application_starts_and_openapi_is_available():
    response = client.get("/openapi.json")

    assert response.status_code == 200

    schema = response.json()
    assert isinstance(schema.get("paths"), dict)
    assert len(schema["paths"]) > 0


def test_every_registered_api_path_has_http_operations():
    paths = client.get("/openapi.json").json()["paths"]

    assert paths

    for path, path_item in paths.items():
        http_methods = {
            method
            for method in path_item
            if method in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
                "options",
                "head",
                "trace",
            }
        }

        assert http_methods, (
            f"No HTTP operations registered for {path}"
        )


# ==================================================
# 2. MODULE 45-50 ROUTE REGISTRATION SMOKE TESTS
# ==================================================

def test_supplier_invoice_routes_are_registered():
    paths = client.get("/openapi.json").json()["paths"]

    assert "/supplier-invoices" in paths
    assert "/supplier-invoices/{invoice_id}" in paths
    assert "/supplier-invoices/{invoice_id}/status" in paths

    assert "get" in paths["/supplier-invoices"]
    assert "post" in paths["/supplier-invoices"]
    assert "get" in paths["/supplier-invoices/{invoice_id}"]
    assert (
        "patch"
        in paths["/supplier-invoices/{invoice_id}/status"]
    )


def test_audit_log_routes_are_registered():
    paths = client.get("/openapi.json").json()["paths"]

    assert "/audit-logs" in paths
    assert "get" in paths["/audit-logs"]
    assert "post" in paths["/audit-logs"]


def test_dashboard_route_is_registered():
    paths = client.get("/openapi.json").json()["paths"]

    assert "/dashboard/summary" in paths
    assert "get" in paths["/dashboard/summary"]


def test_reporting_routes_are_registered():
    paths = client.get("/openapi.json").json()["paths"]

    expected_routes = [
        "/reports/purchase-orders",
        "/reports/supplier-invoices",
        "/reports/summary",
    ]

    for path in expected_routes:
        assert path in paths, f"Missing report route: {path}"
        assert "get" in paths[path]


def test_csv_export_routes_are_registered():
    paths = client.get("/openapi.json").json()["paths"]

    expected_routes = [
        "/report-exports/purchase-orders.csv",
        "/report-exports/supplier-invoices.csv",
        "/report-exports/purchase-orders/background",
    ]

    for path in expected_routes:
        assert path in paths, f"Missing export route: {path}"

    assert "get" in paths["/report-exports/purchase-orders.csv"]
    assert "get" in paths["/report-exports/supplier-invoices.csv"]


# ==================================================
# 3. SUPPLIER INVOICE VALIDATION TESTS
# ==================================================

def valid_invoice_payload():
    return {
        "invoice_number": "INV-TEST-001",
        "supplier_id": 1,
        "invoice_date": "2026-10-20",
        "due_date": "2026-11-20",
        "currency": "INR",
        "subtotal": "1000.00",
        "tax_amount": "180.00",
    }


def test_supplier_invoice_accepts_valid_payload():
    invoice = SupplierInvoiceCreate(**valid_invoice_payload())

    assert invoice.invoice_number == "INV-TEST-001"
    assert invoice.supplier_id == 1
    assert invoice.invoice_date == date(2026, 10, 20)
    assert invoice.subtotal == Decimal("1000.00")
    assert invoice.tax_amount == Decimal("180.00")


def test_supplier_invoice_rejects_negative_subtotal():
    payload = valid_invoice_payload()
    payload["subtotal"] = "-1.00"

    with pytest.raises(ValidationError):
        SupplierInvoiceCreate(**payload)


def test_supplier_invoice_rejects_negative_tax():
    payload = valid_invoice_payload()
    payload["tax_amount"] = "-1.00"

    with pytest.raises(ValidationError):
        SupplierInvoiceCreate(**payload)


def test_supplier_invoice_rejects_due_date_before_invoice_date():
    payload = valid_invoice_payload()
    payload["invoice_date"] = "2026-10-20"
    payload["due_date"] = "2026-10-19"

    with pytest.raises(
        ValidationError,
        match="Due date cannot be earlier than invoice date",
    ):
        SupplierInvoiceCreate(**payload)


def test_supplier_invoice_rejects_invalid_supplier_id():
    payload = valid_invoice_payload()
    payload["supplier_id"] = 0

    with pytest.raises(ValidationError):
        SupplierInvoiceCreate(**payload)


def test_supplier_invoice_status_accepts_allowed_values():
    for status_value in ("APPROVED", "PAID", "VOID"):
        result = SupplierInvoiceStatusUpdate(
            status=status_value
        )
        assert result.status == status_value


def test_supplier_invoice_status_rejects_unsupported_value():
    with pytest.raises(ValidationError):
        SupplierInvoiceStatusUpdate(status="PENDING")


# ==================================================
# 4. AUDIT LOG SCHEMA VALIDATION TESTS
# ==================================================

def test_audit_log_accepts_valid_payload():
    log = AuditLogCreate(
        action="CREATE",
        entity_type="SUPPLIER_INVOICE",
        entity_id="123",
        details="Invoice created",
    )

    assert log.action == "CREATE"
    assert log.entity_type == "SUPPLIER_INVOICE"
    assert log.entity_id == "123"


def test_audit_log_rejects_empty_action():
    with pytest.raises(ValidationError):
        AuditLogCreate(
            action="",
            entity_type="SUPPLIER_INVOICE",
        )


def test_audit_log_rejects_empty_entity_type():
    with pytest.raises(ValidationError):
        AuditLogCreate(
            action="CREATE",
            entity_type="",
        )


# ==================================================
# 5. AUDIT SERVICE UNIT TESTS
# These use a mock session and never write to MySQL.
# ==================================================

def test_audit_service_serializes_dictionary_details():
    db = MagicMock()

    entry = write_audit_log(
        db,
        actor_user_id=7,
        action="CREATE",
        entity_type="SUPPLIER_INVOICE",
        entity_id=123,
        details={"invoice_number": "INV-TEST-001"},
    )

    assert entry.actor_user_id == 7
    assert entry.action == "CREATE"
    assert entry.entity_type == "SUPPLIER_INVOICE"
    assert entry.entity_id == "123"
    assert json.loads(entry.details) == {
        "invoice_number": "INV-TEST-001"
    }

    db.add.assert_called_once_with(entry)
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(entry)


def test_audit_service_preserves_string_details():
    db = MagicMock()

    entry = write_audit_log(
        db,
        actor_user_id=7,
        action="READ",
        entity_type="REPORT",
        details="Report viewed",
    )

    assert entry.details == "Report viewed"


def test_audit_service_can_skip_commit():
    db = MagicMock()

    entry = write_audit_log(
        db,
        actor_user_id=None,
        action="READ",
        entity_type="REPORT",
        details="Report viewed",
        commit=False,
    )

    assert entry.actor_user_id is None
    assert entry.details == "Report viewed"

    db.add.assert_called_once_with(entry)
    db.commit.assert_not_called()
    db.refresh.assert_not_called()
