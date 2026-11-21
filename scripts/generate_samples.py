"""Regenerates samples/sample_invoices.csv and samples/sample_invoices.xlsx.

    python scripts/generate_samples.py

The rows refer to the vendors / POs created by `manage.py seed_demo_data`
and are chosen to produce every classification (see the README).
"""
import csv
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"

COLUMNS = [
    "invoice_number", "vendor_code", "po_number", "invoice_date", "due_date",
    "amount", "tax_amount", "total_amount",
]

# invoice_number, vendor, PO, invoice date, due date, amount, tax, total   -> expected
ROWS = [
    ("INV-1001", "V001", "PO-1001", date(2026, 2, 10), date(2026, 3, 12), 10000, 1800, 11800),  # MATCHED
    ("INV-1002", "V001", "PO-1001", date(2026, 2, 15), date(2026, 3, 17), 5000, 900, 5900),     # MATCHED
    ("INV-2001", "V002", "PO-2001", date(2026, 2, 20), date(2026, 3, 22), 8000, 1440, 9440),    # MATCHED
    ("INV-1003", "V001", "PO-1002", date(2026, 3, 1), date(2026, 3, 31), 3000, 540, 3540),      # MISMATCHED: exceeds PO balance
    ("INV-2002", "V002", "PO-1001", date(2026, 3, 2), date(2026, 4, 1), 1000, 180, 1180),       # MISMATCHED: PO belongs to V001
    ("INV-1001", "V001", "PO-1001", date(2026, 2, 10), date(2026, 3, 12), 10000, 1800, 11800),  # DUPLICATE of row 2
    ("INV-9001", "V999", "PO-1001", date(2026, 3, 3), date(2026, 4, 2), 1000, 180, 1180),       # FAILED: unknown vendor
    ("INV-1004", "V001", "PO-9999", date(2026, 3, 4), date(2026, 4, 3), 1000, 180, 1180),       # FAILED: unknown PO
    ("INV-3001", "V003", "PO-3001", date(2026, 3, 5), date(2026, 4, 4), 1000, 180, 1180),       # FAILED: inactive vendor
    ("INV-1005", "V001", "PO-1001", date(2026, 3, 6), date(2026, 3, 1), 1000, 180, 1180),       # FAILED: due before invoice date
    ("INV-1006", "V001", "PO-1001", date(2026, 3, 7), date(2026, 4, 6), "abc", 180, 1180),      # FAILED: amount not a number
    ("INV-1007", "V001", "PO-1001", date(2026, 3, 8), date(2026, 4, 7), 1000, 180, 1500),       # FAILED: total != amount + tax
    ("INV-4001", "V004", "PO-4001", date(2026, 3, 9), date(2026, 4, 8), 1000, 180, 1180),       # FAILED: cancelled PO
    ("INV-2003", "V002", "PO-2001", date(2026, 3, 10), date(2026, 4, 9), 2500, None, None),     # MATCHED: tax/total omitted
]


def write_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in ROWS:
            writer.writerow(
                ["" if v is None else v.isoformat() if isinstance(v, date) else v for v in row]
            )


def write_xlsx(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Invoices"
    sheet.append(COLUMNS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in ROWS:
        sheet.append(list(row))
    for column, width in zip("ABCDEFGH", (16, 13, 12, 14, 14, 12, 12, 14)):
        sheet.column_dimensions[column].width = width
    workbook.save(path)


if __name__ == "__main__":
    SAMPLES_DIR.mkdir(exist_ok=True)
    write_csv(SAMPLES_DIR / "sample_invoices.csv")
    write_xlsx(SAMPLES_DIR / "sample_invoices.xlsx")
    print(f"Wrote {len(ROWS)} rows to {SAMPLES_DIR}")
