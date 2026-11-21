"""CSV / XLSX parsing (file-level behaviour only)."""
import datetime as dt

import pytest

from fastapi_app.exceptions import FileValidationError
from fastapi_app.services.file_parser import parse_file
from tests.helpers import invoice_row, make_csv, make_xlsx


def parse(filename, content, max_rows=100):
    return parse_file(filename, content, max_rows=max_rows)


def error_code(filename, content, **kw):
    with pytest.raises(FileValidationError) as excinfo:
        parse(filename, content, **kw)
    return excinfo.value.code


def test_csv_rows_and_row_numbers():
    rows = parse("a.csv", make_csv([invoice_row("INV-1"), invoice_row("INV-2")]))
    assert [r.row_number for r in rows] == [2, 3]
    assert rows[0].data["invoice_number"] == "INV-1"
    assert rows[1].data["total_amount"] == "1180.00"


def test_csv_with_bom_and_semicolon_delimiter():
    content = (
        "﻿invoice_number;vendor_code;po_number;invoice_date;due_date;amount\n"
        "INV-1;V001;PO-001;2026-06-01;2026-07-01;100\n"
    ).encode("utf-8")
    rows = parse("a.csv", content)
    assert rows[0].data == {
        "invoice_number": "INV-1", "vendor_code": "V001", "po_number": "PO-001",
        "invoice_date": "2026-06-01", "due_date": "2026-07-01", "amount": "100",
    }


def test_header_aliases_case_and_unknown_columns():
    content = (
        b"Invoice No,Vendor,PO Reference,Invoice Date,Due Date,Subtotal,Tax,Total,Comment\n"
        b"INV-1,V001,PO-001,2026-06-01,2026-07-01,100,18,118,ignore me\n"
    )
    data = parse("a.csv", content)[0].data
    assert data["invoice_number"] == "INV-1"
    assert data["po_number"] == "PO-001"
    assert data["amount"] == "100"
    assert data["tax_amount"] == "18"
    assert data["total_amount"] == "118"
    assert "comment" not in data


def test_blank_lines_are_skipped_but_row_numbers_are_preserved():
    content = make_csv([invoice_row("INV-1")]) + b",,,,,,,\n\n" + make_csv(
        [invoice_row("INV-2")]
    ).split(b"\n", 1)[1]
    rows = parse("a.csv", content)
    assert [(r.row_number, r.data["invoice_number"]) for r in rows] == [(2, "INV-1"), (5, "INV-2")]


def test_short_rows_are_padded_with_none():
    content = b"invoice_number,vendor_code,po_number,invoice_date,due_date,amount\nINV-1,V001\n"
    data = parse("a.csv", content)[0].data
    assert data["po_number"] is None
    assert data["amount"] is None


def test_xlsx_keeps_native_types():
    row = invoice_row(invoice_date=dt.date(2026, 6, 1), amount=1000.5)
    data = parse("a.xlsx", make_xlsx([row]))[0].data
    assert data["invoice_date"] == dt.datetime(2026, 6, 1)
    assert data["amount"] == 1000.5
    assert data["invoice_number"] == "INV-001"


@pytest.mark.parametrize("filename", ["a.txt", "a.xls", "a.pdf", "noextension", None])
def test_unsupported_file_type(filename):
    assert error_code(filename, b"x") == "unsupported_file_type"


@pytest.mark.parametrize("content", [b"", b"\n\n", b" , , \n"])
def test_empty_file(content):
    assert error_code("a.csv", content) == "empty_file"


def test_header_only_file():
    assert error_code("a.csv", make_csv([])) == "no_data_rows"


def test_missing_required_columns_are_listed():
    with pytest.raises(FileValidationError) as excinfo:
        parse("a.csv", b"invoice_number,vendor_code,amount\nINV-1,V001,100\n")
    assert excinfo.value.code == "missing_columns"
    assert excinfo.value.details["missing"] == ["po_number", "invoice_date", "due_date"]


def test_duplicate_header_columns():
    content = (
        b"invoice_number,invoice_no,vendor_code,po_number,invoice_date,due_date,amount\n"
        b"A,B,V,P,2026-06-01,2026-07-01,1\n"
    )
    assert error_code("a.csv", content) == "invalid_header"


def test_corrupt_xlsx():
    assert error_code("a.xlsx", b"this is not a zip archive") == "unreadable_file"


def test_row_limit():
    content = make_csv([invoice_row(f"INV-{i}") for i in range(4)])
    assert len(parse("a.csv", content, max_rows=4)) == 4
    assert error_code("a.csv", content, max_rows=3) == "too_many_rows"
