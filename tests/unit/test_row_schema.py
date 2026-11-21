"""Pydantic validation of a single uploaded row."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from fastapi_app.schemas import validate_row

VALID = {
    "invoice_number": "inv-001",
    "vendor_code": " v001 ",
    "po_number": "po-001",
    "invoice_date": "2026-06-01",
    "due_date": "2026-07-01",
    "amount": "1000",
    "tax_amount": "180",
    "total_amount": "1180",
}


def row_with(**overrides):
    return {**VALID, **overrides}


def test_valid_row_is_parsed_and_normalised():
    row, errors = validate_row(VALID)
    assert errors == []
    assert row.invoice_number == "INV-001"
    assert row.vendor_code == "V001"
    assert row.po_number == "PO-001"
    assert row.invoice_date == date(2026, 6, 1)
    assert row.amount == Decimal("1000.00")
    assert row.total_amount == Decimal("1180.00")


@pytest.mark.parametrize(
    "value",
    ["2026-06-01", "01-06-2026", "01/06/2026", "2026/06/01", "01-Jun-2026",
     "2026-06-01 00:00:00", datetime(2026, 6, 1, 0, 0), date(2026, 6, 1)],
)
def test_supported_date_formats(value):
    row, errors = validate_row(row_with(invoice_date=value))
    assert errors == []
    assert row.invoice_date == date(2026, 6, 1)


@pytest.mark.parametrize("value", ["not-a-date", "2026-13-45", "31/02/2026", 12345])
def test_invalid_dates_are_rejected(value):
    row, errors = validate_row(row_with(invoice_date=value))
    assert row is None
    assert errors[0]["field"] == "invoice_date"
    assert errors[0]["code"] == "INVALID_FIELD"


@pytest.mark.parametrize(
    "field", ["invoice_number", "vendor_code", "po_number", "invoice_date", "due_date", "amount"]
)
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_required_fields(field, blank):
    row, errors = validate_row(row_with(**{field: blank}))
    assert row is None
    assert [e["field"] for e in errors] == [field]
    assert "required" in errors[0]["message"]


def test_missing_key_is_reported():
    data = dict(VALID)
    del data["po_number"]
    row, errors = validate_row(data)
    assert row is None
    assert errors[0]["field"] == "po_number"


def test_tax_and_total_are_optional():
    row, errors = validate_row(row_with(tax_amount="", total_amount=None))
    assert errors == []
    assert row.tax_amount == Decimal("0.00")
    assert row.total_amount == Decimal("1000.00")


@pytest.mark.parametrize(
    "raw, expected",
    [("1,250.50", "1250.50"), (1250.5, "1250.50"), (1250, "1250.00"), ("  99.999 ", "100.00")],
)
def test_amount_coercion(raw, expected):
    row, errors = validate_row(row_with(amount=raw, tax_amount=0, total_amount=None))
    assert errors == []
    assert row.amount == Decimal(expected)


@pytest.mark.parametrize("raw", ["abc", "-5", "0", "NaN", "1e20", True])
def test_bad_amounts_are_rejected(raw):
    row, errors = validate_row(row_with(amount=raw))
    assert row is None
    assert errors[0]["field"] == "amount"


def test_negative_tax_is_rejected():
    row, errors = validate_row(row_with(tax_amount="-1"))
    assert row is None
    assert errors[0]["field"] == "tax_amount"


def test_excel_numeric_identifier_is_not_rendered_as_float():
    row, _ = validate_row(row_with(invoice_number=1001.0, po_number=555))
    assert row.invoice_number == "1001"
    assert row.po_number == "555"


def test_overlong_identifier_is_rejected():
    row, errors = validate_row(row_with(invoice_number="X" * 51))
    assert row is None
    assert errors[0]["field"] == "invoice_number"


def test_all_field_errors_are_collected():
    row, errors = validate_row(row_with(invoice_date="bad", amount="bad", vendor_code=""))
    assert row is None
    assert {e["field"] for e in errors} == {"invoice_date", "amount", "vendor_code"}
