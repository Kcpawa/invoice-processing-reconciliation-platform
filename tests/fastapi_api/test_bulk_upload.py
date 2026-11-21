"""POST /api/v1/uploads/invoices - parsing, classification and persistence."""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from fastapi_app.config import Settings
from fastapi_app.models import Invoice, UploadBatch, UploadRecord
from tests.helpers import INVOICE_DATE, TODAY, invoice_row, make_csv, make_xlsx


def by_row(body):
    """row_number -> record, across valid and invalid lists."""
    return {r["row_number"]: r for r in body["valid_records"] + body["invalid_records"]}


def reason_codes(record):
    return [e["code"] for e in record["errors"]]


def invoice_count(session):
    return session.scalar(select(func.count()).select_from(Invoice))


@pytest.fixture
def vendor_and_po(make_vendor, make_po):
    vendor = make_vendor("V001")
    return vendor, make_po(vendor, "PO-001", amount="10000.00")


def test_valid_rows_are_imported_matched_and_reduce_po_balance(
    upload, sa_session, vendor_and_po
):
    _, po = vendor_and_po
    response = upload(make_csv([invoice_row("INV-1"), invoice_row("INV-2")]))

    assert response.status_code == 201
    body = response.json()
    assert body["idempotent_replay"] is False
    summary = body["summary"]
    assert summary["status"] == "COMPLETED"
    assert (summary["total_records"], summary["success_count"], summary["failure_count"],
            summary["duplicate_count"], summary["matched_count"]) == (2, 2, 0, 0, 2)
    assert [r["classification"] for r in body["valid_records"]] == ["MATCHED", "MATCHED"]
    assert body["invalid_records"] == []

    sa_session.refresh(po)
    assert po.remaining_balance == Decimal("7640.00")  # 10000 - 2 * 1180
    invoices = sa_session.scalars(select(Invoice).order_by(Invoice.id)).all()
    assert [i.invoice_number for i in invoices] == ["INV-1", "INV-2"]
    assert {i.reconciliation_status for i in invoices} == {"MATCHED"}
    assert {i.source for i in invoices} == {"BULK_UPLOAD"}
    assert body["valid_records"][0]["invoice_id"] == invoices[0].id


def test_mixed_file_separates_valid_and_invalid_records(
    upload, sa_session, make_vendor, make_po
):
    v1 = make_vendor("V001")
    v2 = make_vendor("V002")
    v3 = make_vendor("V003", status="INACTIVE")
    make_po(v1, "PO-001", amount="2000.00")
    make_po(v2, "PO-002", amount="5000.00")
    make_po(v3, "PO-003")

    rows = [
        invoice_row("INV-1"),                                         # 2 matched
        invoice_row("INV-2"),                                         # 3 exceeds balance
        invoice_row("INV-3", vendor_code="V002"),                     # 4 PO of another vendor
        invoice_row("INV-1"),                                         # 5 duplicate in file
        invoice_row("INV-4", vendor_code="NOPE"),                     # 6 unknown vendor
        invoice_row("INV-5", po_number="PO-404"),                     # 7 unknown PO
        invoice_row("INV-6", vendor_code="V003", po_number="PO-003"), # 8 inactive vendor
        invoice_row("INV-7", amount="abc"),                           # 9 bad amount
        invoice_row("", vendor_code="V001"),                          # 10 missing number
        invoice_row("INV-8", due_date=(INVOICE_DATE - timedelta(days=1)).isoformat()),  # 11
        invoice_row("INV-9", total_amount="9999.00"),                 # 12 total != amount+tax
        invoice_row("INV-10", invoice_date=(TODAY + timedelta(days=2)).isoformat(),
                    due_date=(TODAY + timedelta(days=30)).isoformat()),  # 13 future date
        invoice_row("INV-11", vendor_code="V002", po_number="PO-002"),  # 14 matched
    ]
    response = upload(make_csv(rows))
    assert response.status_code == 201
    body = response.json()
    records = by_row(body)

    expected = {
        2: ("MATCHED", []),
        3: ("MISMATCHED", ["EXCEEDS_PO_BALANCE"]),
        4: ("MISMATCHED", ["VENDOR_PO_MISMATCH", "EXCEEDS_PO_BALANCE"]),  # 820 left
        5: ("DUPLICATE", ["DUPLICATE_INVOICE"]),
        6: ("FAILED", ["VENDOR_NOT_FOUND"]),
        7: ("FAILED", ["PO_NOT_FOUND"]),
        8: ("FAILED", ["VENDOR_INACTIVE"]),
        9: ("FAILED", ["INVALID_FIELD"]),
        10: ("FAILED", ["INVALID_FIELD"]),
        11: ("FAILED", ["DUE_DATE_BEFORE_INVOICE_DATE"]),
        12: ("FAILED", ["TOTAL_MISMATCH"]),
        13: ("FAILED", ["INVOICE_DATE_IN_FUTURE"]),
        14: ("MATCHED", []),
    }
    actual = {n: (r["classification"], reason_codes(r)) for n, r in records.items()}
    assert actual == expected

    summary = body["summary"]
    assert summary["total_records"] == 13
    assert summary["matched_count"] == 2
    assert summary["mismatched_count"] == 2
    assert summary["success_count"] == 4
    assert summary["duplicate_count"] == 1
    assert summary["failure_count"] == 8
    assert (summary["success_count"] + summary["failure_count"]
            + summary["duplicate_count"]) == summary["total_records"]

    # valid = stored as invoices; invalid = rejected
    assert sorted(r["row_number"] for r in body["valid_records"]) == [2, 3, 4, 14]
    assert all(r["invoice_id"] for r in body["valid_records"])
    assert all(r["invoice_id"] is None for r in body["invalid_records"])
    assert invoice_count(sa_session) == 4


def test_mismatched_invoice_is_stored_without_consuming_balance(
    upload, sa_session, make_vendor, make_po
):
    vendor = make_vendor("V001")
    po = make_po(vendor, "PO-001", amount="500.00")

    body = upload(make_csv([invoice_row("INV-1")])).json()

    record = body["valid_records"][0]
    assert record["classification"] == "MISMATCHED"
    assert "exceeds the remaining PO balance 500.00" in record["errors"][0]["message"]
    invoice = sa_session.get(Invoice, record["invoice_id"])
    assert invoice.reconciliation_status == "MISMATCHED"
    assert invoice.reconciliation_notes[0]["code"] == "EXCEEDS_PO_BALANCE"
    sa_session.refresh(po)
    assert po.remaining_balance == Decimal("500.00")
    assert po.status == "OPEN"


def test_balance_is_tracked_row_by_row_and_po_closes_at_zero(
    upload, sa_session, make_vendor, make_po
):
    vendor = make_vendor("V001")
    po = make_po(vendor, "PO-001", amount="2360.00")  # room for exactly two invoices

    body = upload(make_csv([invoice_row(f"INV-{i}") for i in (1, 2, 3)])).json()

    assert [r["classification"] for r in by_row(body).values()] == [
        "MATCHED", "MATCHED", "MISMATCHED",
    ]
    third = by_row(body)[4]
    # PO was closed by row 3, so the third invoice reports both problems.
    assert set(reason_codes(third)) == {"PO_CLOSED", "EXCEEDS_PO_BALANCE"}
    sa_session.refresh(po)
    assert po.remaining_balance == Decimal("0.00")
    assert po.status == "CLOSED"


def test_invoice_already_in_database_is_a_duplicate(
    upload, sa_session, vendor_and_po, make_invoice
):
    vendor, po = vendor_and_po
    make_invoice(vendor, po, "INV-1")

    body = upload(make_csv([invoice_row("inv-1"), invoice_row("INV-2")])).json()

    records = by_row(body)
    assert records[2]["classification"] == "DUPLICATE"
    assert "already exists for vendor V001" in records[2]["errors"][0]["message"]
    assert records[3]["classification"] == "MATCHED"
    assert body["summary"]["duplicate_count"] == 1
    assert invoice_count(sa_session) == 2


def test_same_invoice_number_for_different_vendors_is_not_a_duplicate(
    upload, make_vendor, make_po
):
    make_po(make_vendor("V001"), "PO-001")
    make_po(make_vendor("V002"), "PO-002")

    body = upload(make_csv([
        invoice_row("INV-1"),
        invoice_row("INV-1", vendor_code="V002", po_number="PO-002"),
    ])).json()

    assert body["summary"]["matched_count"] == 2
    assert body["summary"]["duplicate_count"] == 0


def test_identifiers_are_matched_case_insensitively(upload, vendor_and_po):
    body = upload(make_csv([invoice_row("inv-1", vendor_code="v001", po_number=" po-001 ")])).json()
    record = body["valid_records"][0]
    assert record["classification"] == "MATCHED"
    assert (record["invoice_number"], record["vendor_code"], record["po_number"]) == (
        "INV-1", "V001", "PO-001",
    )


def test_cancelled_po_fails_and_invoice_before_po_date_mismatches(
    upload, make_vendor, make_po
):
    vendor = make_vendor("V001")
    make_po(vendor, "PO-CANCELLED", status="CANCELLED")
    make_po(vendor, "PO-NEW", order_date=TODAY - timedelta(days=1))

    body = upload(make_csv([
        invoice_row("INV-1", po_number="PO-CANCELLED"),
        invoice_row("INV-2", po_number="PO-NEW"),
    ])).json()

    records = by_row(body)
    assert (records[2]["classification"], reason_codes(records[2])) == ("FAILED", ["PO_CANCELLED"])
    assert (records[3]["classification"], reason_codes(records[3])) == (
        "MISMATCHED", ["INVOICE_BEFORE_PO_DATE"],
    )


def test_xlsx_upload_with_native_dates_and_numbers(upload, sa_session, vendor_and_po):
    rows = [
        invoice_row("INV-1", invoice_date=INVOICE_DATE, due_date=TODAY, amount=1000,
                    tax_amount=180, total_amount=1180),
        invoice_row(1002.0, invoice_date=INVOICE_DATE, due_date=TODAY, amount=50.5,
                    tax_amount=None, total_amount=None),
    ]
    response = upload(make_xlsx(rows), filename="invoices.xlsx")

    assert response.status_code == 201
    body = response.json()
    assert body["summary"]["file_type"] == "xlsx"
    assert body["summary"]["matched_count"] == 2
    second = sa_session.scalars(select(Invoice).where(Invoice.invoice_number == "1002")).one()
    assert second.total_amount == Decimal("50.50")
    assert second.tax_amount == Decimal("0.00")


def test_raw_row_data_is_kept_for_failed_rows(upload, sa_session, vendor_and_po):
    upload(make_csv([invoice_row("INV-1", amount="abc")]))
    record = sa_session.scalars(select(UploadRecord)).one()
    assert record.classification == "FAILED"
    assert record.raw_data["amount"] == "abc"
    assert record.invoice_number == "INV-1"
    assert record.invoice_id is None


@pytest.mark.parametrize(
    "filename, content, status_code, code",
    [
        ("invoices.pdf", b"%PDF", 400, "unsupported_file_type"),
        ("invoices.csv", b"", 400, "empty_file"),
        ("invoices.csv", b"invoice_number,amount\nINV-1,10\n", 400, "missing_columns"),
        ("invoices.xlsx", b"not a workbook", 400, "unreadable_file"),
    ],
)
def test_file_level_errors_reject_the_upload_without_creating_a_batch(
    upload, sa_session, filename, content, status_code, code
):
    response = upload(content, filename=filename)
    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert sa_session.scalar(select(func.count()).select_from(UploadBatch)) == 0


def test_missing_file_field_is_a_validation_error(client):
    response = client.post("/api/v1/uploads/invoices")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


class TestLimits:
    @pytest.fixture
    def app_settings(self):
        return Settings(_env_file=None, max_upload_size_mb=1, max_upload_rows=2)

    def test_oversized_file_is_rejected(self, upload):
        response = upload(b"x" * (1024 * 1024 + 1))
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "file_too_large"

    def test_too_many_rows_is_rejected(self, upload, vendor_and_po):
        response = upload(make_csv([invoice_row(f"INV-{i}") for i in range(3)]))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "too_many_rows"
