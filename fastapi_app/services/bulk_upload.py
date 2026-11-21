"""Bulk invoice upload: parse -> validate -> classify -> persist -> summarise.

Transaction design
------------------
1. The batch row is inserted and committed first (status PROCESSING). Its
   unique idempotency key is the lock: a concurrent request with the same
   key hits the unique constraint instead of processing the file twice.
2. All rows are then processed in ONE transaction, together with the batch
   counters. Either the whole file is applied or none of it is.
3. Each imported row runs inside a SAVEPOINT, so an unexpected constraint
   violation on one row downgrades that row instead of aborting the batch.
4. If processing crashes, everything is rolled back and the batch is marked
   FAILED in a fresh transaction; the same request can then be retried.

Idempotency
-----------
The key is the client's `Idempotency-Key` header, or `sha256:<file content>`
when the header is absent. Re-sending a processed upload returns the stored
result without touching invoices or PO balances again.
"""
from __future__ import annotations

import hashlib
import logging
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fastapi_app.config import Settings
from fastapi_app.exceptions import (
    ConflictError,
    FileValidationError,
    NotFoundError,
    PayloadTooLargeError,
    ProcessingError,
)
from fastapi_app.models import (
    Invoice,
    PurchaseOrder,
    UploadBatch,
    UploadRecord,
    Vendor,
    utcnow,
)
from fastapi_app.schemas import InvoiceRow, validate_row
from fastapi_app.services.file_parser import ParsedRow, detect_file_type, parse_file, to_jsonable
from fastapi_app.services.reconciliation import (
    consume_po_balance,
    po_snapshot,
    vendor_snapshot,
)
from shared.enums import BatchStatus, InvoiceSource, RecordClassification
from shared.rules import InvoiceCandidate, Reason, RuleResult, evaluate

logger = logging.getLogger(__name__)

IMPORTED = {RecordClassification.MATCHED, RecordClassification.MISMATCHED}
MAX_IDEMPOTENCY_KEY_LENGTH = 255


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------
def _find_batch_by_key(session: Session, key: str) -> UploadBatch | None:
    return session.execute(
        select(UploadBatch)
        .where(UploadBatch.idempotency_key == key)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _is_stale(batch: UploadBatch, settings: Settings) -> bool:
    started = batch.created_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return utcnow() - started > timedelta(minutes=settings.stale_batch_minutes)


def _should_replay(
    existing: UploadBatch, *, explicit_key: str | None, file_hash: str, settings: Settings
) -> bool:
    """True -> return the stored result. False -> the batch may be re-run.
    Raises when the request conflicts with the existing batch."""
    if explicit_key and existing.file_hash != file_hash:
        raise ConflictError(
            "This Idempotency-Key was already used with a different file.",
            code="idempotency_key_reused",
            details={"batch_id": existing.id},
        )
    if existing.status == BatchStatus.COMPLETED.value:
        return True
    if existing.status == BatchStatus.PROCESSING.value and not _is_stale(existing, settings):
        raise ConflictError(
            "This upload is already being processed.",
            code="upload_in_progress",
            details={"batch_id": existing.id},
        )
    return False  # FAILED, or PROCESSING left behind by a crashed worker


# --------------------------------------------------------------------------
# Row processing
# --------------------------------------------------------------------------
def _clip(value, length: int) -> str:
    return "" if value is None else str(value).strip()[:length]


def _process_rows(
    session: Session, batch: UploadBatch, rows: list[ParsedRow], today: date
) -> Counter:
    validated: list[tuple[ParsedRow, InvoiceRow | None, list[dict]]] = [
        (parsed, *validate_row(parsed.data)) for parsed in rows
    ]
    good_rows = [row for _, row, _ in validated if row is not None]

    # Reference data for the whole file in three queries (no per-row lookups).
    vendor_codes = {row.vendor_code for row in good_rows}
    po_numbers = {row.po_number for row in good_rows}
    vendors: dict[str, Vendor] = {}
    if vendor_codes:
        vendors = {
            v.vendor_code: v
            for v in session.scalars(select(Vendor).where(Vendor.vendor_code.in_(vendor_codes)))
        }
    purchase_orders: dict[str, PurchaseOrder] = {}
    if po_numbers:
        # Lock every PO the file touches, in id order to avoid deadlocks.
        purchase_orders = {
            po.po_number: po
            for po in session.scalars(
                select(PurchaseOrder)
                .where(PurchaseOrder.po_number.in_(po_numbers))
                .order_by(PurchaseOrder.id)
                .with_for_update(of=PurchaseOrder)
                .execution_options(populate_existing=True)
            )
        }
    existing_invoices: set[tuple[int, str]] = set()
    if vendors:
        existing_invoices = {
            (vendor_id, number)
            for vendor_id, number in session.execute(
                select(Invoice.vendor_id, Invoice.invoice_number).where(
                    Invoice.vendor_id.in_([v.id for v in vendors.values()]),
                    Invoice.invoice_number.in_({row.invoice_number for row in good_rows}),
                )
            )
        }

    first_seen_at: dict[tuple[str, str], int] = {}
    counts: Counter = Counter()

    for parsed, row, errors in validated:
        invoice: Invoice | None = None

        if row is None:
            classification = RecordClassification.FAILED
        else:
            vendor = vendors.get(row.vendor_code)
            po = purchase_orders.get(row.po_number)

            key = (row.vendor_code, row.invoice_number)
            duplicate_reason = None
            if key in first_seen_at:
                duplicate_reason = (
                    f"Invoice {row.invoice_number} for vendor {row.vendor_code} "
                    f"already appears in this file at row {first_seen_at[key]}."
                )
            elif vendor is not None and (vendor.id, row.invoice_number) in existing_invoices:
                duplicate_reason = (
                    f"Invoice {row.invoice_number} already exists for vendor "
                    f"{row.vendor_code}."
                )
            first_seen_at.setdefault(key, parsed.row_number)

            result = evaluate(
                InvoiceCandidate(
                    invoice_number=row.invoice_number,
                    invoice_date=row.invoice_date,
                    due_date=row.due_date,
                    amount=row.amount,
                    tax_amount=row.tax_amount,
                    total_amount=row.total_amount,
                ),
                vendor_snapshot(vendor),
                po_snapshot(po),
                duplicate_reason=duplicate_reason,
                today=today,
            )
            if result.status in IMPORTED:
                invoice, result = _import_invoice(session, row, vendor, po, result)
            classification = result.status
            errors = result.reasons_as_dicts()

        counts[classification] += 1
        session.add(
            UploadRecord(
                batch_id=batch.id,
                row_number=parsed.row_number,
                invoice_number=row.invoice_number if row else _clip(parsed.data.get("invoice_number"), 50),
                vendor_code=row.vendor_code if row else _clip(parsed.data.get("vendor_code"), 30),
                po_number=row.po_number if row else _clip(parsed.data.get("po_number"), 50),
                classification=classification.value,
                errors=errors,
                raw_data={k: to_jsonable(v) for k, v in parsed.data.items()},
                invoice_id=invoice.id if invoice else None,
            )
        )

    session.flush()
    return counts


def _import_invoice(
    session: Session, row: InvoiceRow, vendor: Vendor, po: PurchaseOrder, result: RuleResult
) -> tuple[Invoice | None, RuleResult]:
    """Insert the invoice (and consume PO balance if MATCHED) in a SAVEPOINT."""
    invoice = Invoice(
        invoice_number=row.invoice_number,
        vendor_id=vendor.id,
        purchase_order_id=po.id,
        invoice_date=row.invoice_date,
        due_date=row.due_date,
        amount=row.amount,
        tax_amount=row.tax_amount,
        total_amount=row.total_amount,
        reconciliation_status=result.status.value,
        reconciliation_notes=result.reasons_as_dicts(),
        reconciled_at=utcnow(),
        source=InvoiceSource.BULK_UPLOAD.value,
    )
    try:
        with session.begin_nested():
            session.add(invoice)
            if result.is_matched:
                consume_po_balance(po, row.total_amount)
            session.flush()
        return invoice, result
    except IntegrityError as exc:
        # The savepoint is rolled back; the batch transaction carries on.
        # Most likely another transaction inserted the same invoice after our
        # pre-check - the unique constraint is the final arbiter.
        logger.warning("Row for invoice %s rejected by the database: %s",
                       row.invoice_number, exc.orig)
        already_exists = session.execute(
            select(Invoice.id).where(
                Invoice.vendor_id == vendor.id, Invoice.invoice_number == row.invoice_number
            )
        ).first()
        if already_exists:
            return None, RuleResult(
                RecordClassification.DUPLICATE,
                [Reason("DUPLICATE_INVOICE",
                        f"Invoice {row.invoice_number} already exists for vendor "
                        f"{row.vendor_code}.")],
            )
        return None, RuleResult(
            RecordClassification.FAILED,
            [Reason("DB_CONSTRAINT_VIOLATION", "The row violates a database constraint.")],
        )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def process_upload(
    session: Session,
    *,
    filename: str | None,
    content: bytes,
    idempotency_key: str | None,
    force: bool,
    settings: Settings,
    today: date | None = None,
) -> tuple[UploadBatch, bool]:
    """Returns (batch, idempotent_replay)."""
    if len(content) > settings.max_upload_size_bytes:
        raise PayloadTooLargeError(
            f"The file exceeds the {settings.max_upload_size_mb} MB upload limit."
        )
    file_type = detect_file_type(filename)

    explicit_key = (idempotency_key or "").strip() or None
    if explicit_key and len(explicit_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise FileValidationError(
            f"Idempotency-Key must be at most {MAX_IDEMPOTENCY_KEY_LENGTH} characters.",
            code="invalid_idempotency_key",
        )
    file_hash = hashlib.sha256(content).hexdigest()
    key = explicit_key or (None if force else f"sha256:{file_hash}")

    existing = _find_batch_by_key(session, key) if key else None
    if existing is not None and _should_replay(
        existing, explicit_key=explicit_key, file_hash=file_hash, settings=settings
    ):
        session.commit()
        return existing, True

    # File-level validation happens before a new batch is recorded.
    rows = parse_file(filename, content, max_rows=settings.max_upload_rows)

    if existing is not None:  # retry of a FAILED / abandoned batch
        batch = existing
        session.execute(delete(UploadRecord).where(UploadRecord.batch_id == batch.id))
        batch.status = BatchStatus.PROCESSING.value
        batch.error_message = ""
        batch.created_at = utcnow()
        batch.completed_at = None
    else:
        batch = UploadBatch(
            idempotency_key=key,
            file_name=_clip(filename, 255),
            file_type=file_type,
            file_hash=file_hash,
            file_size=len(content),
            status=BatchStatus.PROCESSING.value,
        )
        session.add(batch)
    try:
        session.commit()
    except IntegrityError:
        # Lost a race: another request claimed the same key a moment ago.
        session.rollback()
        winner = _find_batch_by_key(session, key) if key else None
        if winner is None:
            raise
        if _should_replay(winner, explicit_key=explicit_key, file_hash=file_hash,
                          settings=settings):
            session.commit()
            return winner, True
        raise ConflictError(
            "This upload is already being processed.",
            code="upload_in_progress",
            details={"batch_id": winner.id},
        ) from None

    batch_id = batch.id
    try:
        counts = _process_rows(session, batch, rows, today or date.today())
        batch.total_records = len(rows)
        batch.matched_count = counts[RecordClassification.MATCHED]
        batch.mismatched_count = counts[RecordClassification.MISMATCHED]
        batch.duplicate_count = counts[RecordClassification.DUPLICATE]
        batch.failed_count = counts[RecordClassification.FAILED]
        batch.status = BatchStatus.COMPLETED.value
        batch.completed_at = utcnow()
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("Bulk upload batch %s failed", batch_id)
        failed = session.get(UploadBatch, batch_id)
        if failed is not None:
            failed.status = BatchStatus.FAILED.value
            failed.error_message = f"{type(exc).__name__}: {exc}"[:1000]
            failed.completed_at = utcnow()
            session.commit()
        raise ProcessingError(
            "The upload could not be processed; no records were imported. "
            "It is safe to retry the same request.",
            details={"batch_id": batch_id},
        ) from exc

    return batch, False


# --------------------------------------------------------------------------
# History queries
# --------------------------------------------------------------------------
def get_batch(session: Session, batch_id: int) -> UploadBatch:
    batch = session.get(UploadBatch, batch_id)
    if batch is None:
        raise NotFoundError(f"Upload batch {batch_id} not found.")
    return batch


def get_batch_records(session: Session, batch_id: int) -> list[UploadRecord]:
    return list(
        session.scalars(
            select(UploadRecord)
            .where(UploadRecord.batch_id == batch_id)
            .order_by(UploadRecord.row_number)
        )
    )


def split_records(
    records: list[UploadRecord],
) -> tuple[list[UploadRecord], list[UploadRecord]]:
    imported = {c.value for c in IMPORTED}
    valid = [r for r in records if r.classification in imported]
    invalid = [r for r in records if r.classification not in imported]
    return valid, invalid


def parse_date_bound(value: date | None, *, end: bool = False) -> datetime | None:
    """Inclusive date filter -> timezone-aware datetime bound."""
    if value is None:
        return None
    bound = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return bound + timedelta(days=1) if end else bound
