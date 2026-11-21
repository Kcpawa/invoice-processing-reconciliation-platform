"""Builders for upload files used across the test-suite."""
import csv
import io
from datetime import date, timedelta

from openpyxl import Workbook

COLUMNS = [
    "invoice_number", "vendor_code", "po_number", "invoice_date", "due_date",
    "amount", "tax_amount", "total_amount",
]

TODAY = date.today()
PO_DATE = TODAY - timedelta(days=60)
INVOICE_DATE = TODAY - timedelta(days=10)
DUE_DATE = TODAY + timedelta(days=20)


def invoice_row(invoice_number="INV-001", vendor_code="V001", po_number="PO-001", **overrides):
    """A valid row: amount 1000 + tax 180 = 1180."""
    row = {
        "invoice_number": invoice_number,
        "vendor_code": vendor_code,
        "po_number": po_number,
        "invoice_date": INVOICE_DATE.isoformat(),
        "due_date": DUE_DATE.isoformat(),
        "amount": "1000.00",
        "tax_amount": "180.00",
        "total_amount": "1180.00",
    }
    row.update(overrides)
    return row


def make_csv(rows, columns=None) -> bytes:
    columns = columns or COLUMNS
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def make_xlsx(rows, columns=None) -> bytes:
    columns = columns or COLUMNS
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(columns)
    for row in rows:
        sheet.append([row.get(column) for column in columns])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
