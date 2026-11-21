"""Pure reconciliation rule engine.

No ORM, no I/O: callers load the vendor / purchase order, answer the
"is this a duplicate?" question, and pass plain snapshots in. That keeps the
business rules in one place and trivially unit-testable.

Classification precedence (first hit wins):

1. DUPLICATE  - the invoice was already received (same vendor + number).
2. FAILED     - the invoice itself is invalid (unknown/inactive vendor,
                unknown/cancelled PO, bad dates, bad amounts).
3. MISMATCHED - the invoice is valid but disagrees with its PO
                (wrong vendor, closed PO, dated before the PO, over balance).
4. MATCHED    - everything agrees; the PO balance may be consumed.

Duplicates are checked by the caller before business validation, so a
re-submitted invoice is reported as DUPLICATE even if its vendor has since
been deactivated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from shared.enums import POStatus, RecordClassification, VendorStatus

TWO_PLACES = Decimal("0.01")
AMOUNT_TOLERANCE = Decimal("0.01")


def money(value) -> Decimal:
    """Coerce to a 2-decimal-place Decimal (drivers may hand back floats)."""
    return Decimal(str(value)).quantize(TWO_PLACES)


def normalize_code(value: str) -> str:
    """Canonical form for vendor codes, PO numbers and invoice numbers."""
    return " ".join(str(value).split()).upper()


@dataclass(frozen=True)
class VendorSnapshot:
    id: int
    vendor_code: str
    status: str


@dataclass(frozen=True)
class POSnapshot:
    id: int
    po_number: str
    vendor_id: int
    status: str
    order_date: date
    remaining_balance: Decimal


@dataclass(frozen=True)
class InvoiceCandidate:
    invoice_number: str
    invoice_date: date
    due_date: date
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal


@dataclass(frozen=True)
class Reason:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass
class RuleResult:
    status: RecordClassification
    reasons: list[Reason] = field(default_factory=list)

    @property
    def is_matched(self) -> bool:
        return self.status == RecordClassification.MATCHED

    def reasons_as_dicts(self) -> list[dict[str, str]]:
        return [r.as_dict() for r in self.reasons]


def _validation_failures(
    inv: InvoiceCandidate,
    vendor: VendorSnapshot | None,
    po: POSnapshot | None,
    today: date,
) -> list[Reason]:
    failures: list[Reason] = []

    if vendor is None:
        failures.append(Reason("VENDOR_NOT_FOUND", "Vendor does not exist."))
    elif vendor.status != VendorStatus.ACTIVE.value:
        failures.append(
            Reason("VENDOR_INACTIVE", f"Vendor {vendor.vendor_code} is not active.")
        )

    if po is None:
        failures.append(Reason("PO_NOT_FOUND", "Purchase order does not exist."))
    elif po.status == POStatus.CANCELLED.value:
        failures.append(
            Reason("PO_CANCELLED", f"Purchase order {po.po_number} is cancelled.")
        )

    if inv.invoice_date > today:
        failures.append(
            Reason("INVOICE_DATE_IN_FUTURE", "Invoice date cannot be in the future.")
        )
    if inv.due_date < inv.invoice_date:
        failures.append(
            Reason("DUE_DATE_BEFORE_INVOICE_DATE", "Due date is before the invoice date.")
        )

    if inv.amount <= 0:
        failures.append(Reason("INVALID_AMOUNT", "Amount must be greater than zero."))
    if inv.tax_amount < 0:
        failures.append(Reason("INVALID_TAX", "Tax amount cannot be negative."))
    if abs(inv.total_amount - (inv.amount + inv.tax_amount)) > AMOUNT_TOLERANCE:
        failures.append(
            Reason(
                "TOTAL_MISMATCH",
                f"Total {inv.total_amount} does not equal amount + tax "
                f"({inv.amount + inv.tax_amount}).",
            )
        )
    return failures


def _mismatches(
    inv: InvoiceCandidate, vendor: VendorSnapshot, po: POSnapshot
) -> list[Reason]:
    mismatches: list[Reason] = []

    if po.vendor_id != vendor.id:
        mismatches.append(
            Reason(
                "VENDOR_PO_MISMATCH",
                f"Purchase order {po.po_number} does not belong to vendor "
                f"{vendor.vendor_code}.",
            )
        )
    if po.status == POStatus.CLOSED.value:
        mismatches.append(
            Reason("PO_CLOSED", f"Purchase order {po.po_number} is closed.")
        )
    if inv.invoice_date < po.order_date:
        mismatches.append(
            Reason(
                "INVOICE_BEFORE_PO_DATE",
                f"Invoice date {inv.invoice_date} is before the PO order date "
                f"{po.order_date}.",
            )
        )
    if inv.total_amount > po.remaining_balance:
        mismatches.append(
            Reason(
                "EXCEEDS_PO_BALANCE",
                f"Invoice total {inv.total_amount} exceeds the remaining PO "
                f"balance {po.remaining_balance}.",
            )
        )
    return mismatches


def evaluate(
    invoice: InvoiceCandidate,
    vendor: VendorSnapshot | None,
    po: POSnapshot | None,
    *,
    duplicate_reason: str | None = None,
    today: date | None = None,
) -> RuleResult:
    """Classify an invoice. ``duplicate_reason`` is set by the caller when the
    invoice duplicates one that was already received."""
    if duplicate_reason:
        return RuleResult(
            RecordClassification.DUPLICATE,
            [Reason("DUPLICATE_INVOICE", duplicate_reason)],
        )

    failures = _validation_failures(invoice, vendor, po, today or date.today())
    if failures:
        return RuleResult(RecordClassification.FAILED, failures)

    mismatches = _mismatches(invoice, vendor, po)  # type: ignore[arg-type]
    if mismatches:
        return RuleResult(RecordClassification.MISMATCHED, mismatches)

    return RuleResult(RecordClassification.MATCHED)
