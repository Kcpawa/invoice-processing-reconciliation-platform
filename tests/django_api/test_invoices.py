from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.invoices.models import Invoice
from tests.helpers import DUE_DATE, INVOICE_DATE, TODAY

pytestmark = pytest.mark.django_db
URL = "/api/v1/invoices/"


@pytest.fixture
def po(vendor, make_po):
    return make_po(vendor, "PO-001", amount="5000.00")


def payload(for_vendor, for_po, **overrides):
    data = {
        "invoice_number": "inv-001", "vendor": for_vendor.id, "purchase_order": for_po.id,
        "invoice_date": INVOICE_DATE.isoformat(), "due_date": DUE_DATE.isoformat(),
        "amount": "1000.00", "tax_amount": "180.00",
    }
    data.update(overrides)
    return data


def test_create_invoice_is_pending_and_total_is_derived(api, vendor, po):
    response = api.post(URL, payload(vendor, po))

    assert response.status_code == 201
    body = response.json()
    assert body["invoice_number"] == "INV-001"
    assert (body["vendor_code"], body["po_number"]) == ("V001", "PO-001")
    assert body["total_amount"] == "1180.00"
    assert body["reconciliation_status"] == "PENDING"
    assert body["reconciliation_notes"] == []
    assert body["source"] == "API"
    po.refresh_from_db()
    assert po.remaining_balance == Decimal("5000.00")  # only reconciliation consumes balance


def test_supplied_total_must_equal_amount_plus_tax(api, vendor, po):
    assert api.post(URL, payload(vendor, po, total_amount="1180.00")).status_code == 201

    response = api.post(URL, payload(vendor, po, invoice_number="INV-2", total_amount="1200.00"))
    assert response.status_code == 400
    assert "amount + tax" in response.json()["error"]["details"]["total_amount"][0]


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"due_date": (INVOICE_DATE - timedelta(days=1)).isoformat()}, "due_date"),
        ({"invoice_date": (TODAY + timedelta(days=1)).isoformat(),
          "due_date": (TODAY + timedelta(days=9)).isoformat()}, "invoice_date"),
        ({"amount": "0"}, "amount"),
        ({"tax_amount": "-5"}, "tax_amount"),
        ({"invoice_number": "  "}, "invoice_number"),
        ({"vendor": 9999}, "vendor"),
        ({"purchase_order": 9999}, "purchase_order"),
        ({"invoice_date": "31-31-2026"}, "invoice_date"),
    ],
)
def test_create_invoice_validation(api, vendor, po, overrides, field):
    response = api.post(URL, payload(vendor, po, **overrides))
    assert response.status_code == 400
    assert field in response.json()["error"]["details"]
    assert not Invoice.objects.exists()


def test_invoice_number_is_unique_per_vendor(api, vendor, po, make_vendor, make_po):
    assert api.post(URL, payload(vendor, po)).status_code == 201

    duplicate = api.post(URL, payload(vendor, po, invoice_number=" INV-001 "))
    assert duplicate.status_code == 400
    assert "already has an invoice" in duplicate.json()["error"]["details"]["invoice_number"][0]

    other = make_vendor("V002")
    other_po = make_po(other, "PO-002")
    assert api.post(URL, payload(other, other_po)).status_code == 201  # other vendor: fine


def test_invoice_against_another_vendors_po_is_accepted_for_reconciliation(
    api, vendor, make_vendor, make_po
):
    """The CRUD API stores it; the reconciliation engine flags it MISMATCHED."""
    other_po = make_po(make_vendor("V002"), "PO-002")
    response = api.post(URL, payload(vendor, other_po))
    assert response.status_code == 201
    assert response.json()["reconciliation_status"] == "PENDING"


def test_reconciliation_fields_are_read_only(api, vendor, po):
    body = api.post(URL, payload(vendor, po, reconciliation_status="MATCHED",
                                 source="BULK_UPLOAD")).json()
    assert (body["reconciliation_status"], body["source"]) == ("PENDING", "API")


def test_editing_a_matched_invoice_releases_balance_and_resets_status(api, matched_invoice):
    po = matched_invoice.purchase_order

    response = api.patch(f"{URL}{matched_invoice.id}/", {"amount": "2000.00"})

    assert response.status_code == 200
    body = response.json()
    assert (body["total_amount"], body["reconciliation_status"]) == ("2000.00", "PENDING")
    assert body["reconciled_at"] is None
    po.refresh_from_db()
    assert po.remaining_balance == Decimal("5000.00")  # the 1,000 was handed back


def test_non_financial_no_op_update_keeps_matched_status(api, matched_invoice):
    po = matched_invoice.purchase_order

    response = api.patch(f"{URL}{matched_invoice.id}/", {"amount": "1000.00"})  # unchanged

    assert response.json()["reconciliation_status"] == "MATCHED"
    po.refresh_from_db()
    assert po.remaining_balance == Decimal("4000.00")


def test_moving_matched_invoice_to_another_po_releases_the_original_po(
    api, vendor, matched_invoice, make_po
):
    original_po = matched_invoice.purchase_order
    new_po = make_po(vendor, "PO-NEW", amount="9000.00")

    response = api.patch(f"{URL}{matched_invoice.id}/", {"purchase_order": new_po.id})

    assert response.json()["reconciliation_status"] == "PENDING"
    original_po.refresh_from_db()
    new_po.refresh_from_db()
    assert original_po.remaining_balance == Decimal("5000.00")
    assert new_po.remaining_balance == Decimal("9000.00")


def test_releasing_balance_reopens_a_closed_po(api, vendor, make_po, make_invoice):
    po = make_po(vendor, "PO-FULL", amount="1000.00", remaining_balance=Decimal("0.00"),
                 status="CLOSED")
    invoice = make_invoice(vendor, po, amount="1000.00", reconciliation_status="MATCHED")

    assert api.delete(f"{URL}{invoice.id}/").status_code == 204

    po.refresh_from_db()
    assert (po.remaining_balance, po.status) == (Decimal("1000.00"), "OPEN")
    assert not Invoice.objects.exists()


def test_deleting_an_unmatched_invoice_leaves_the_balance_alone(api, vendor, po, make_invoice):
    invoice = make_invoice(vendor, po, reconciliation_status="MISMATCHED")
    assert api.delete(f"{URL}{invoice.id}/").status_code == 204
    po.refresh_from_db()
    assert po.remaining_balance == Decimal("5000.00")


def test_update_cannot_create_a_duplicate_number(api, vendor, po, make_invoice):
    make_invoice(vendor, po, "INV-001")
    second = make_invoice(vendor, po, "INV-002")

    response = api.patch(f"{URL}{second.id}/", {"invoice_number": "inv-001"})

    assert response.status_code == 400
    assert "invoice_number" in response.json()["error"]["details"]


def test_list_filters_and_pagination(api, vendor, po, make_vendor, make_po, make_invoice):
    other = make_vendor("V002")
    other_po = make_po(other, "PO-002")
    make_invoice(vendor, po, "INV-001", amount="100.00", reconciliation_status="MATCHED")
    make_invoice(vendor, po, "INV-002", amount="200.00", reconciliation_status="MISMATCHED",
                 source="BULK_UPLOAD")
    make_invoice(other, other_po, "INV-003", amount="300.00",
                 invoice_date=TODAY, due_date=TODAY + timedelta(days=45))

    def numbers(**params):
        return [i["invoice_number"] for i in api.get(URL, params).json()["results"]]

    assert numbers() == ["INV-001", "INV-002", "INV-003"]
    assert numbers(reconciliation_status="MISMATCHED") == ["INV-002"]
    assert numbers(source="BULK_UPLOAD") == ["INV-002"]
    assert numbers(vendor_code="v002") == ["INV-003"]
    assert numbers(po_number="PO-001") == ["INV-001", "INV-002"]
    assert numbers(purchase_order=other_po.id) == ["INV-003"]
    assert numbers(invoice_date_from=TODAY.isoformat()) == ["INV-003"]
    assert numbers(due_date_to=(TODAY + timedelta(days=30)).isoformat()) == ["INV-001", "INV-002"]
    assert numbers(min_total="150", max_total="250") == ["INV-002"]
    assert numbers(search="acme", ordering="-total_amount") == ["INV-002", "INV-001"]

    page = api.get(URL, {"page_size": 2, "page": 2}).json()
    assert (page["count"], page["total_pages"]) == (3, 2)
    assert [i["invoice_number"] for i in page["results"]] == ["INV-003"]
    assert api.get(URL, {"page": 99}).status_code == 404


@pytest.mark.parametrize(
    "bad_values",
    [
        {"due_date": INVOICE_DATE - timedelta(days=1)},
        {"amount": Decimal("0.00")},
        {"tax_amount": Decimal("-1.00")},
        {"reconciliation_status": "DUPLICATE"},  # duplicates are never stored
    ],
)
def test_database_check_constraints(vendor, po, make_invoice, bad_values):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_invoice(vendor, po, **bad_values)


def test_database_unique_constraint_per_vendor(vendor, po, make_invoice):
    make_invoice(vendor, po, "INV-001")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_invoice(vendor, po, "INV-001")


def test_swagger_schema_is_served(api, db):
    response = api.get("/api/schema/", {"format": "json"})
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert {"/api/v1/vendors/", "/api/v1/purchase-orders/", "/api/v1/invoices/"} <= set(paths)
    assert api.get("/api/docs/").status_code == 200
    assert api.get("/health/").json()["status"] == "ok"
