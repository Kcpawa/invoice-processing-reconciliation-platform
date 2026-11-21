"""Pydantic models: upload-row validation and API response contracts."""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Generic, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from shared.enums import BatchStatus, RecordClassification, ReconciliationStatus
from shared.rules import TWO_PLACES, normalize_code

T = TypeVar("T")

# Ambiguous numeric dates are read day-first (25/12/2026, 05-01-2026 = 5 Jan).
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y")
MAX_MONEY = Decimal("999999999999.99")  # NUMERIC(14, 2)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_date(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        # Excel exports often carry a midnight time component.
        if text.endswith(" 00:00:00"):
            text = text[: -len(" 00:00:00")]
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        raise ValueError(
            f"'{value}' is not a valid date (expected YYYY-MM-DD or DD/MM/YYYY)"
        )
    raise ValueError("not a valid date")


def _parse_money(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("not a valid amount")
    if isinstance(value, str):
        value = value.strip().replace(",", "")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"'{value}' is not a valid amount") from None
    if not amount.is_finite():
        raise ValueError(f"'{value}' is not a valid amount")
    if abs(amount) > MAX_MONEY:
        raise ValueError("amount is too large")
    return amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


class InvoiceRow(BaseModel):
    """One data row of an uploaded file.

    Checks structure only (required fields, types, ranges). Cross-entity
    business rules live in shared.rules so they are applied identically to
    uploaded rows and to invoices created through the CRUD API.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    invoice_number: str = Field(min_length=1, max_length=50)
    vendor_code: str = Field(min_length=1, max_length=30)
    po_number: str = Field(min_length=1, max_length=50)
    invoice_date: date
    due_date: date
    amount: Decimal = Field(gt=0)
    tax_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    total_amount: Decimal | None = Field(default=None, gt=0)

    @field_validator("invoice_number", "vendor_code", "po_number", mode="before")
    @classmethod
    def _normalize_identifier(cls, value: Any) -> Any:
        if _is_blank(value):
            raise ValueError("field is required")
        if isinstance(value, float) and value.is_integer():
            value = int(value)  # Excel turns "1001" into 1001.0
        return normalize_code(str(value))

    @field_validator("invoice_date", "due_date", mode="before")
    @classmethod
    def _coerce_date(cls, value: Any) -> Any:
        if _is_blank(value):
            raise ValueError("field is required")
        return _parse_date(value)

    @field_validator("amount", mode="before")
    @classmethod
    def _coerce_amount(cls, value: Any) -> Any:
        if _is_blank(value):
            raise ValueError("field is required")
        return _parse_money(value)

    @field_validator("tax_amount", mode="before")
    @classmethod
    def _coerce_tax(cls, value: Any) -> Any:
        return Decimal("0.00") if _is_blank(value) else _parse_money(value)

    @field_validator("total_amount", mode="before")
    @classmethod
    def _coerce_total(cls, value: Any) -> Any:
        return None if _is_blank(value) else _parse_money(value)

    @model_validator(mode="after")
    def _default_total(self) -> InvoiceRow:
        if self.total_amount is None:
            self.total_amount = self.amount + self.tax_amount
        return self


def validate_row(data: dict[str, Any]) -> tuple[InvoiceRow | None, list[dict[str, str]]]:
    """Returns (row, []) or (None, errors)."""
    try:
        return InvoiceRow.model_validate(data), []
    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"]) or "row"
            message = err["msg"].removeprefix("Value error, ")
            errors.append(
                {"code": "INVALID_FIELD", "field": field, "message": f"{field}: {message}"}
            )
        return None, errors


# --------------------------------------------------------------------------
# Response models
# --------------------------------------------------------------------------
class Page(BaseModel, Generic[T]):
    count: int
    page: int
    page_size: int
    total_pages: int
    results: list[T]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class UploadRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int
    invoice_number: str
    vendor_code: str
    po_number: str
    classification: RecordClassification
    errors: list[dict[str, Any]]
    invoice_id: int | None


class UploadBatchOut(BaseModel):
    """Processing summary of one upload.

    success_count + failure_count + duplicate_count == total_records.
    success = rows imported as invoices (matched_count + mismatched_count).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    file_name: str
    file_type: str
    file_hash: str
    file_size: int
    idempotency_key: str | None
    status: BatchStatus
    total_records: int
    success_count: int
    failure_count: int
    duplicate_count: int
    matched_count: int
    mismatched_count: int
    error_message: str
    created_at: datetime
    completed_at: datetime | None


class UploadResultOut(BaseModel):
    idempotent_replay: bool = Field(
        description="True when this upload was already processed and the stored "
        "result is being returned; nothing was processed again."
    )
    summary: UploadBatchOut
    valid_records: list[UploadRecordOut] = Field(
        description="Rows imported as invoices (MATCHED or MISMATCHED)."
    )
    invalid_records: list[UploadRecordOut] = Field(
        description="Rows rejected (FAILED or DUPLICATE), with reasons."
    )


class ReconciliationRunRequest(BaseModel):
    vendor_code: str | None = Field(default=None, description="Only this vendor's invoices.")
    po_number: str | None = Field(default=None, description="Only invoices on this PO.")
    include_failed: bool = Field(
        default=False,
        description="Also retry FAILED invoices (PENDING and MISMATCHED are always included).",
    )
    limit: int = Field(default=500, ge=1, le=5000)


class InvoiceReconciliationOut(BaseModel):
    invoice_id: int
    invoice_number: str
    vendor_code: str
    po_number: str
    total_amount: Decimal
    previous_status: ReconciliationStatus
    status: ReconciliationStatus
    reasons: list[dict[str, Any]]
    po_remaining_balance: Decimal


class ReconciliationRunOut(BaseModel):
    processed: int
    matched: int
    mismatched: int
    failed: int
    results: list[InvoiceReconciliationOut]


class VendorSummaryOut(BaseModel):
    vendor_id: int
    vendor_code: str
    vendor_name: str
    vendor_status: str
    invoice_count: int
    total_invoiced_amount: Decimal
    matched_count: int
    matched_amount: Decimal
    mismatched_count: int
    mismatched_amount: Decimal
    pending_count: int
    failed_count: int
    first_invoice_date: date | None
    last_invoice_date: date | None


class POBalanceOut(BaseModel):
    po_id: int
    po_number: str
    vendor_code: str
    vendor_name: str
    status: str
    order_date: date
    total_amount: Decimal
    invoiced_amount: Decimal
    remaining_balance: Decimal
    utilization_percent: Decimal
    matched_invoice_count: int
    unmatched_invoice_count: int


class POBalanceTotals(BaseModel):
    total_amount: Decimal
    invoiced_amount: Decimal
    remaining_balance: Decimal


class POBalanceReport(Page[POBalanceOut]):
    totals: POBalanceTotals = Field(
        description="Totals across every PO matching the filters (not just this page)."
    )


class UploadStats(BaseModel):
    total_batches: int
    batches_by_status: dict[str, int]
    total_records: int
    success_count: int
    failure_count: int
    duplicate_count: int
    matched_count: int
    mismatched_count: int
    success_rate_percent: Decimal


class StatusBucket(BaseModel):
    count: int
    total_amount: Decimal


class InvoiceStats(BaseModel):
    total_invoices: int
    total_amount: Decimal
    by_status: dict[str, StatusBucket]
    by_source: dict[str, int]


class ProcessingStatsOut(BaseModel):
    date_from: date | None
    date_to: date | None
    uploads: UploadStats
    invoices: InvoiceStats
