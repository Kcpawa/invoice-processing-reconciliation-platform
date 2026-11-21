"""Reconciliation of invoices already stored in the database."""
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.helpers import TODAY

RUN = "/api/v1/reconciliation/run"


@pytest.fixture
def vendor(make_vendor):
    return make_vendor("V001")


def test_pending_invoice_within_balance_is_matched(
    client, sa_session, vendor, make_po, make_invoice
):
    po = make_po(vendor, "PO-001", amount="5000.00")
    invoice = make_invoice(vendor, po, "INV-1", amount="1000.00", tax="180.00")

    response = client.post(RUN)

    assert response.status_code == 200
    body = response.json()
    assert (body["processed"], body["matched"], body["mismatched"], body["failed"]) == (1, 1, 0, 0)
    result = body["results"][0]
    assert result["invoice_id"] == invoice.id
    assert (result["previous_status"], result["status"]) == ("PENDING", "MATCHED")
    assert result["po_remaining_balance"] == "3820.00"

    sa_session.refresh(invoice)
    sa_session.refresh(po)
    assert invoice.reconciliation_status == "MATCHED"
    assert invoice.reconciled_at is not None
    assert invoice.reconciliation_notes == []
    assert po.remaining_balance == Decimal("3820.00")


def test_invoices_compete_for_balance_in_id_order(
    client, sa_session, vendor, make_po, make_invoice
):
    po = make_po(vendor, "PO-001", amount="1500.00")
    first = make_invoice(vendor, po, "INV-1", amount="1000.00")
    second = make_invoice(vendor, po, "INV-2", amount="1000.00")

    body = client.post(RUN).json()

    statuses = {r["invoice_id"]: r["status"] for r in body["results"]}
    assert statuses == {first.id: "MATCHED", second.id: "MISMATCHED"}
    reasons = next(r for r in body["results"] if r["invoice_id"] == second.id)["reasons"]
    assert reasons[0]["code"] == "EXCEEDS_PO_BALANCE"
    sa_session.refresh(po)
    assert po.remaining_balance == Decimal("500.00")


def test_running_twice_never_double_deducts(client, sa_session, vendor, make_po, make_invoice):
    po = make_po(vendor, "PO-001", amount="5000.00")
    make_invoice(vendor, po, "INV-1", amount="1000.00")

    client.post(RUN)
    second = client.post(RUN).json()

    assert second["processed"] == 0
    sa_session.refresh(po)
    assert po.remaining_balance == Decimal("4000.00")


def test_mismatched_invoice_is_matched_after_po_is_corrected(
    client, sa_session, vendor, make_po, make_invoice
):
    po = make_po(vendor, "PO-001", amount="500.00")
    invoice = make_invoice(vendor, po, "INV-1", amount="1000.00")
    assert client.post(RUN).json()["mismatched"] == 1

    po.amount = po.total_amount = Decimal("2000.00")  # PO amended upwards
    po.remaining_balance = Decimal("2000.00")
    sa_session.commit()

    body = client.post(RUN).json()

    assert body["matched"] == 1
    assert body["results"][0]["previous_status"] == "MISMATCHED"
    sa_session.refresh(invoice)
    sa_session.refresh(po)
    assert invoice.reconciliation_status == "MATCHED"
    assert po.remaining_balance == Decimal("1000.00")


def test_po_closes_when_fully_invoiced(client, sa_session, vendor, make_po, make_invoice):
    po = make_po(vendor, "PO-001", amount="1000.00")
    make_invoice(vendor, po, "INV-1", amount="1000.00")

    client.post(RUN)

    sa_session.refresh(po)
    assert (po.remaining_balance, po.status) == (Decimal("0.00"), "CLOSED")


def test_vendor_po_mismatch(client, make_vendor, vendor, make_po, make_invoice):
    other = make_vendor("V002")
    po = make_po(other, "PO-OTHER")
    make_invoice(vendor, po, "INV-1")

    result = client.post(RUN).json()["results"][0]

    assert result["status"] == "MISMATCHED"
    assert [r["code"] for r in result["reasons"]] == ["VENDOR_PO_MISMATCH"]


def test_failed_invoices_are_retried_only_on_request(
    client, sa_session, vendor, make_po, make_invoice
):
    po = make_po(vendor, "PO-001")
    invoice = make_invoice(vendor, po, "INV-1")
    vendor.status = "INACTIVE"
    sa_session.commit()

    first = client.post(RUN).json()
    assert first["failed"] == 1
    assert first["results"][0]["reasons"][0]["code"] == "VENDOR_INACTIVE"

    vendor.status = "ACTIVE"
    sa_session.commit()
    assert client.post(RUN).json()["processed"] == 0  # FAILED is not retried by default

    retried = client.post(RUN, json={"include_failed": True}).json()
    assert retried["matched"] == 1
    sa_session.refresh(invoice)
    assert invoice.reconciliation_status == "MATCHED"


def test_run_filters_and_limit(client, make_vendor, vendor, make_po, make_invoice):
    other = make_vendor("V002")
    po1 = make_po(vendor, "PO-001")
    po2 = make_po(other, "PO-002")
    make_invoice(vendor, po1, "INV-1")
    make_invoice(vendor, po1, "INV-2")
    make_invoice(other, po2, "INV-3")

    by_vendor = client.post(RUN, json={"vendor_code": "v002"}).json()
    assert [r["invoice_number"] for r in by_vendor["results"]] == ["INV-3"]

    limited = client.post(RUN, json={"po_number": "PO-001", "limit": 1}).json()
    assert [r["invoice_number"] for r in limited["results"]] == ["INV-1"]

    rest = client.post(RUN, json={}).json()
    assert [r["invoice_number"] for r in rest["results"]] == ["INV-2"]


def test_run_rejects_invalid_parameters(client):
    response = client.post(RUN, json={"limit": 0})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_reconcile_single_invoice(client, sa_session, vendor, make_po, make_invoice):
    po = make_po(vendor, "PO-001", order_date=TODAY - timedelta(days=1))
    invoice = make_invoice(vendor, po, "INV-1")  # dated before the PO

    response = client.post(f"/api/v1/reconciliation/invoices/{invoice.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "MISMATCHED"
    assert body["reasons"][0]["code"] == "INVOICE_BEFORE_PO_DATE"
    assert (body["vendor_code"], body["po_number"]) == ("V001", "PO-001")


def test_reconciling_a_matched_invoice_is_a_no_op(
    client, sa_session, vendor, make_po, make_invoice
):
    po = make_po(vendor, "PO-001", amount="5000.00")
    invoice = make_invoice(vendor, po, "INV-1", amount="1000.00")
    url = f"/api/v1/reconciliation/invoices/{invoice.id}"

    first = client.post(url).json()
    second = client.post(url).json()

    assert (first["previous_status"], first["status"]) == ("PENDING", "MATCHED")
    assert (second["previous_status"], second["status"]) == ("MATCHED", "MATCHED")
    sa_session.refresh(po)
    assert po.remaining_balance == Decimal("4000.00")


def test_reconcile_unknown_invoice_is_404(client):
    response = client.post("/api/v1/reconciliation/invoices/999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
