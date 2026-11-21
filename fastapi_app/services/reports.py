"""Read-only reporting queries (SQLAlchemy Core aggregates)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from math import ceil

from sqlalchemy import Select, and_, case, func, select
from sqlalchemy.orm import Session

from fastapi_app.models import Invoice, PurchaseOrder, UploadBatch, Vendor
from fastapi_app.schemas import (
    InvoiceStats,
    Page,
    POBalanceOut,
    POBalanceReport,
    POBalanceTotals,
    ProcessingStatsOut,
    StatusBucket,
    UploadStats,
    VendorSummaryOut,
)
from fastapi_app.services.bulk_upload import parse_date_bound
from shared.enums import BatchStatus, InvoiceSource, ReconciliationStatus
from shared.rules import money, normalize_code

ZERO = Decimal("0.00")
MATCHED = ReconciliationStatus.MATCHED.value
MISMATCHED = ReconciliationStatus.MISMATCHED.value
PENDING = ReconciliationStatus.PENDING.value
FAILED = ReconciliationStatus.FAILED.value


def total_pages(count: int, page_size: int) -> int:
    return ceil(count / page_size) if count else 0


def paginate(session: Session, stmt: Select, page: int, page_size: int):
    """Returns (total row count, rows of the requested page)."""
    count = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = session.execute(stmt.limit(page_size).offset((page - 1) * page_size)).all()
    return count or 0, rows


def _count_if(condition):
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def _sum_if(condition, column):
    return func.coalesce(func.sum(case((condition, column), else_=0)), 0)


def vendor_invoice_summary(
    session: Session,
    *,
    vendor_code: str | None,
    vendor_status: str | None,
    search: str | None,
    invoice_date_from: date | None,
    invoice_date_to: date | None,
    page: int,
    page_size: int,
) -> Page[VendorSummaryOut]:
    # Date filters go in the JOIN so vendors without invoices in the period
    # still appear with zero totals.
    join_on = [Invoice.vendor_id == Vendor.id]
    if invoice_date_from:
        join_on.append(Invoice.invoice_date >= invoice_date_from)
    if invoice_date_to:
        join_on.append(Invoice.invoice_date <= invoice_date_to)

    status = Invoice.reconciliation_status
    stmt = (
        select(
            Vendor.id,
            Vendor.vendor_code,
            Vendor.name,
            Vendor.status,
            func.count(Invoice.id).label("invoice_count"),
            func.coalesce(func.sum(Invoice.total_amount), 0).label("total_invoiced"),
            _count_if(status == MATCHED).label("matched_count"),
            _sum_if(status == MATCHED, Invoice.total_amount).label("matched_amount"),
            _count_if(status == MISMATCHED).label("mismatched_count"),
            _sum_if(status == MISMATCHED, Invoice.total_amount).label("mismatched_amount"),
            _count_if(status == PENDING).label("pending_count"),
            _count_if(status == FAILED).label("failed_count"),
            func.min(Invoice.invoice_date).label("first_invoice_date"),
            func.max(Invoice.invoice_date).label("last_invoice_date"),
        )
        .select_from(Vendor)
        .outerjoin(Invoice, and_(*join_on))
        .group_by(Vendor.id, Vendor.vendor_code, Vendor.name, Vendor.status)
        .order_by(Vendor.vendor_code)
    )
    if vendor_code:
        stmt = stmt.where(Vendor.vendor_code == normalize_code(vendor_code))
    if vendor_status:
        stmt = stmt.where(Vendor.status == vendor_status)
    if search:
        stmt = stmt.where(Vendor.name.ilike(f"%{search.strip()}%"))

    count, rows = paginate(session, stmt, page, page_size)
    results = [
        VendorSummaryOut(
            vendor_id=r.id,
            vendor_code=r.vendor_code,
            vendor_name=r.name,
            vendor_status=r.status,
            invoice_count=r.invoice_count,
            total_invoiced_amount=money(r.total_invoiced),
            matched_count=r.matched_count,
            matched_amount=money(r.matched_amount),
            mismatched_count=r.mismatched_count,
            mismatched_amount=money(r.mismatched_amount),
            pending_count=r.pending_count,
            failed_count=r.failed_count,
            first_invoice_date=r.first_invoice_date,
            last_invoice_date=r.last_invoice_date,
        )
        for r in rows
    ]
    return Page[VendorSummaryOut](
        count=count, page=page, page_size=page_size,
        total_pages=total_pages(count, page_size), results=results,
    )


def po_balance_report(
    session: Session,
    *,
    vendor_code: str | None,
    po_number: str | None,
    status: str | None,
    has_balance: bool | None,
    order_date_from: date | None,
    order_date_to: date | None,
    page: int,
    page_size: int,
) -> POBalanceReport:
    filters = []
    if vendor_code:
        filters.append(Vendor.vendor_code == normalize_code(vendor_code))
    if po_number:
        filters.append(PurchaseOrder.po_number == normalize_code(po_number))
    if status:
        filters.append(PurchaseOrder.status == status)
    if has_balance is True:
        filters.append(PurchaseOrder.remaining_balance > 0)
    elif has_balance is False:
        filters.append(PurchaseOrder.remaining_balance == 0)
    if order_date_from:
        filters.append(PurchaseOrder.order_date >= order_date_from)
    if order_date_to:
        filters.append(PurchaseOrder.order_date <= order_date_to)

    def invoice_count(condition):
        return (
            select(func.count(Invoice.id))
            .where(Invoice.purchase_order_id == PurchaseOrder.id, condition)
            .correlate(PurchaseOrder)
            .scalar_subquery()
        )

    stmt = (
        select(
            PurchaseOrder.id,
            PurchaseOrder.po_number,
            Vendor.vendor_code,
            Vendor.name.label("vendor_name"),
            PurchaseOrder.status,
            PurchaseOrder.order_date,
            PurchaseOrder.total_amount,
            PurchaseOrder.remaining_balance,
            invoice_count(Invoice.reconciliation_status == MATCHED).label("matched_count"),
            invoice_count(Invoice.reconciliation_status != MATCHED).label("unmatched_count"),
        )
        .select_from(PurchaseOrder)
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .where(*filters)
        .order_by(PurchaseOrder.po_number)
    )
    count, rows = paginate(session, stmt, page, page_size)

    grand = session.execute(
        select(
            func.coalesce(func.sum(PurchaseOrder.total_amount), 0),
            func.coalesce(func.sum(PurchaseOrder.remaining_balance), 0),
        )
        .select_from(PurchaseOrder)
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .where(*filters)
    ).one()
    grand_total, grand_remaining = money(grand[0]), money(grand[1])

    results = []
    for r in rows:
        total, remaining = money(r.total_amount), money(r.remaining_balance)
        invoiced = total - remaining
        results.append(
            POBalanceOut(
                po_id=r.id,
                po_number=r.po_number,
                vendor_code=r.vendor_code,
                vendor_name=r.vendor_name,
                status=r.status,
                order_date=r.order_date,
                total_amount=total,
                invoiced_amount=invoiced,
                remaining_balance=remaining,
                utilization_percent=money(invoiced * 100 / total) if total else ZERO,
                matched_invoice_count=r.matched_count,
                unmatched_invoice_count=r.unmatched_count,
            )
        )
    return POBalanceReport(
        count=count, page=page, page_size=page_size,
        total_pages=total_pages(count, page_size), results=results,
        totals=POBalanceTotals(
            total_amount=grand_total,
            invoiced_amount=grand_total - grand_remaining,
            remaining_balance=grand_remaining,
        ),
    )


def processing_statistics(
    session: Session, *, date_from: date | None, date_to: date | None
) -> ProcessingStatsOut:
    """Upload and invoice statistics, optionally limited to records created
    between two dates (inclusive, UTC)."""
    start = parse_date_bound(date_from)
    end = parse_date_bound(date_to, end=True)

    def created_between(column):
        conditions = []
        if start:
            conditions.append(column >= start)
        if end:
            conditions.append(column < end)
        return conditions

    batch_filter = created_between(UploadBatch.created_at)
    batches_by_status = {status: 0 for status in BatchStatus.values()}
    batches_by_status.update(
        dict(
            session.execute(
                select(UploadBatch.status, func.count())
                .where(*batch_filter)
                .group_by(UploadBatch.status)
            ).all()
        )
    )
    sums = session.execute(
        select(
            func.coalesce(func.sum(UploadBatch.total_records), 0),
            func.coalesce(func.sum(UploadBatch.matched_count), 0),
            func.coalesce(func.sum(UploadBatch.mismatched_count), 0),
            func.coalesce(func.sum(UploadBatch.duplicate_count), 0),
            func.coalesce(func.sum(UploadBatch.failed_count), 0),
        ).where(*batch_filter, UploadBatch.status == BatchStatus.COMPLETED.value)
    ).one()
    total_records, matched, mismatched, duplicates, failed = (int(v) for v in sums)
    success = matched + mismatched
    uploads = UploadStats(
        total_batches=sum(batches_by_status.values()),
        batches_by_status=batches_by_status,
        total_records=total_records,
        success_count=success,
        failure_count=failed,
        duplicate_count=duplicates,
        matched_count=matched,
        mismatched_count=mismatched,
        success_rate_percent=(
            money(Decimal(success) * 100 / total_records) if total_records else ZERO
        ),
    )

    invoice_filter = created_between(Invoice.created_at)
    by_status = {
        status: StatusBucket(count=0, total_amount=ZERO)
        for status in ReconciliationStatus.values()
    }
    for status, count, amount in session.execute(
        select(
            Invoice.reconciliation_status,
            func.count(),
            func.coalesce(func.sum(Invoice.total_amount), 0),
        )
        .where(*invoice_filter)
        .group_by(Invoice.reconciliation_status)
    ):
        by_status[status] = StatusBucket(count=count, total_amount=money(amount))

    by_source = {source: 0 for source in InvoiceSource.values()}
    by_source.update(
        dict(
            session.execute(
                select(Invoice.source, func.count())
                .where(*invoice_filter)
                .group_by(Invoice.source)
            ).all()
        )
    )
    invoices = InvoiceStats(
        total_invoices=sum(b.count for b in by_status.values()),
        total_amount=sum((b.total_amount for b in by_status.values()), ZERO),
        by_status=by_status,
        by_source=by_source,
    )
    return ProcessingStatsOut(
        date_from=date_from, date_to=date_to, uploads=uploads, invoices=invoices
    )
