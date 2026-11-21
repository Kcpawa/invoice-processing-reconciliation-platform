"""Turns an uploaded CSV / XLSX file into a list of raw rows.

Only file-level problems are raised here (unsupported type, unreadable
file, missing columns). Row-level problems are left for validation so that
one bad row never rejects the whole file.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePath
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from fastapi_app.exceptions import FileValidationError

SUPPORTED_EXTENSIONS = {".csv": "csv", ".xlsx": "xlsx"}

REQUIRED_COLUMNS = (
    "invoice_number", "vendor_code", "po_number", "invoice_date", "due_date", "amount",
)
OPTIONAL_COLUMNS = ("tax_amount", "total_amount")

HEADER_ALIASES = {
    "invoice_no": "invoice_number",
    "invoice_num": "invoice_number",
    "invoice": "invoice_number",
    "vendor": "vendor_code",
    "vendor_id": "vendor_code",
    "po": "po_number",
    "po_no": "po_number",
    "po_reference": "po_number",
    "po_ref": "po_number",
    "purchase_order": "po_number",
    "purchase_order_number": "po_number",
    "subtotal": "amount",
    "net_amount": "amount",
    "invoice_amount": "amount",
    "tax": "tax_amount",
    "total": "total_amount",
}


@dataclass(frozen=True)
class ParsedRow:
    row_number: int  # 1-based position in the file; the header is row 1
    data: dict[str, Any]


def detect_file_type(filename: str | None) -> str:
    extension = PurePath(filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise FileValidationError(
            f"Unsupported file type '{extension or 'unknown'}'. Upload a .csv or .xlsx file.",
            code="unsupported_file_type",
        )
    return SUPPORTED_EXTENSIONS[extension]


def _normalize_header(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return HEADER_ALIASES.get(text, text)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _read_csv(content: bytes) -> list[list[Any]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    # Pick the delimiter from the header line (Excel in some locales writes ';').
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max(",;\t|", key=first_line.count)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        return [list(row) for row in reader]
    except csv.Error as exc:
        raise FileValidationError(
            f"The file is not valid CSV: {exc}.", code="unreadable_file"
        ) from None


def _read_xlsx(content: bytes) -> list[list[Any]]:
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, OSError, ValueError):
        raise FileValidationError(
            "The file is not a readable .xlsx workbook.", code="unreadable_file"
        ) from None
    try:
        sheet = workbook.worksheets[0]
        return [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def to_jsonable(value: Any) -> Any:
    """Raw cell value -> something storable in a JSON column."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def parse_file(filename: str | None, content: bytes, *, max_rows: int) -> list[ParsedRow]:
    file_type = detect_file_type(filename)
    if not content:
        raise FileValidationError("The uploaded file is empty.", code="empty_file")

    raw_rows = _read_csv(content) if file_type == "csv" else _read_xlsx(content)

    # The header is the first non-blank row.
    header_index = next(
        (i for i, row in enumerate(raw_rows) if any(not _is_blank(c) for c in row)), None
    )
    if header_index is None:
        raise FileValidationError("The uploaded file is empty.", code="empty_file")

    headers = [_normalize_header(cell) for cell in raw_rows[header_index]]
    named = [h for h in headers if h]
    repeated = sorted({h for h in named if named.count(h) > 1})
    if repeated:
        raise FileValidationError(
            f"Duplicate column(s) in header: {', '.join(repeated)}.", code="invalid_header"
        )
    missing = [column for column in REQUIRED_COLUMNS if column not in headers]
    if missing:
        raise FileValidationError(
            f"Missing required column(s): {', '.join(missing)}.",
            code="missing_columns",
            details={
                "missing": missing,
                "required": list(REQUIRED_COLUMNS),
                "optional": list(OPTIONAL_COLUMNS),
            },
        )

    known = set(REQUIRED_COLUMNS) | set(OPTIONAL_COLUMNS)
    rows: list[ParsedRow] = []
    for offset, cells in enumerate(raw_rows[header_index + 1:], start=header_index + 2):
        if all(_is_blank(cell) for cell in cells):
            continue
        data = {
            header: cells[i] if i < len(cells) else None
            for i, header in enumerate(headers)
            if header in known
        }
        rows.append(ParsedRow(row_number=offset, data=data))
        if len(rows) > max_rows:
            raise FileValidationError(
                f"The file has more than {max_rows} data rows. Split it into smaller files.",
                code="too_many_rows",
            )

    if not rows:
        raise FileValidationError(
            "The file contains a header but no data rows.", code="no_data_rows"
        )
    return rows
