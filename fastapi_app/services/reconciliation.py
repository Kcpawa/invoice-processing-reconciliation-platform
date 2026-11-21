"""Applies the shared reconciliation rules to invoices stored in the database."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi_app.exceptions import NotFoundError
from fastapi_app.models import Invoice, PurchaseOrder, Vendor, utcnow
from fastapi_app.schemas import (
    InvoiceReconciliationOut,
    ReconciliationRunOut,
    ReconciliationRunRequest,
)
from shared.enums import POStatus, ReconciliationStatus
from shared.rules import (
    InvoiceCandidate,
    POSnapshot,
    RuleResult,
    VendorSnapshot,
    evaluate,
    money,
    normalize_code,
)


def vendor_snapshot(vendor: Vendor | None) -> VendorSnapshot | None:
    if vendor is None:
        return None
    return VendorSnapshot(id=vendor.id, vendor_code=vendor.vendor_code, status=vendor.status)


def po_snapshot(po: PurchaseOrder | None) -> POSnapshot | None:
    if po is None:
        return None
    return POSnapshot(
        id=po.id,
        po_number=po.po_number,
        vendor_id=po.vendor_id,
        status=po.status,
        order_date=po.order_date,
        remaining_balance=money(po.remaining_balance),
    )


def lock_purchase_order(session: Session, po_id: int) -> PurchaseOrder:
    """SELECT ... FOR UPDATE, re-reading the row so the balance is current.

    Every code path that changes remaining_balance takes this lock first,
    which is what makes concurrent reconciliations safe.
    """
    return session.execute(
        select(PurchaseOrder)
        .where(PurchaseOrder.id == po_id)
        .with_for_update(of=PurchaseOrder)
        .execution_options(populate_existing=True)
    ).scalar_one()


def consume_po_balance(po: PurchaseOrder, total) -> None:
    """Deduct a matched invoice from its (already locked) purchase order."""
    po.remaining_balance = money(po.remaining_balance) - money(total)
    if po.remaining_balance == 0:
        po.status = POStatus.CLOSED.value


def reconcile_invoice(
    session: Session, invoice: Invoice, *, today: date | None = None
) -> InvoiceReconciliationOut:
    """Classify one stored invoice. Flushes, does not commit.

    A MATCHED invoice has already consumed its PO balance, so it is returned
    unchanged - reconciling twice never double-deducts.
    """
    previous = invoice.reconciliation_status
    po = lock_purchase_order(session, invoice.purchase_order_id)
    reasons = list(invoice.reconciliation_notes or [])

    if previous != ReconciliationStatus.MATCHED.value:
        result: RuleResult = evaluate(
            InvoiceCandidate(
                invoice_number=invoice.invoice_number,
                invoice_date=invoice.invoice_date,
                due_date=invoice.due_date,
                amount=money(invoice.amount),
                tax_amount=money(invoice.tax_amount),
                total_amount=money(invoice.total_amount),
            ),
            vendor_snapshot(invoice.vendor),
            po_snapshot(po),
            today=today,
        )
        if result.is_matched:
            consume_po_balance(po, invoice.total_amount)
        reasons = result.reasons_as_dicts()
        invoice.reconciliation_status = result.status.value
        invoice.reconciliation_notes = reasons
        invoice.reconciled_at = utcnow()
        session.flush()

    return InvoiceReconciliationOut(
        invoice_id=invoice.id,
        invoice_number=invoice.invoice_number,
        vendor_code=invoice.vendor.vendor_code,
        po_number=po.po_number,
        total_amount=money(invoice.total_amount),
        previous_status=previous,
        status=invoice.reconciliation_status,
        reasons=reasons,
        po_remaining_balance=money(po.remaining_balance),
    )


def reconcile_one(session: Session, invoice_id: int) -> InvoiceReconciliationOut:
    invoice = session.execute(
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .with_for_update(of=Invoice)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if invoice is None:
        raise NotFoundError(f"Invoice {invoice_id} not found.")
    outcome = reconcile_invoice(session, invoice)
    session.commit()
    return outcome


def run_reconciliation(
    session: Session, params: ReconciliationRunRequest
) -> ReconciliationRunOut:
    """Reconcile every open (PENDING / MISMATCHED) invoice in one transaction.

    Invoices are taken in purchase-order order so concurrent runs acquire PO
    locks in the same sequence (no deadlocks), and SKIP LOCKED lets a second
    run pass over invoices the first one is already working on.
    """
    statuses = [ReconciliationStatus.PENDING.value, ReconciliationStatus.MISMATCHED.value]
    if params.include_failed:
        statuses.append(ReconciliationStatus.FAILED.value)

    stmt = select(Invoice).where(Invoice.reconciliation_status.in_(statuses))
    if params.vendor_code:
        stmt = stmt.where(
            Invoice.vendor.has(Vendor.vendor_code == normalize_code(params.vendor_code))
        )
    if params.po_number:
        stmt = stmt.where(
            Invoice.purchase_order.has(
                PurchaseOrder.po_number == normalize_code(params.po_number)
            )
        )
    stmt = (
        stmt.order_by(Invoice.purchase_order_id, Invoice.id)
        .limit(params.limit)
        .with_for_update(of=Invoice, skip_locked=True)
        .execution_options(populate_existing=True)
    )

    results = [reconcile_invoice(session, inv) for inv in session.scalars(stmt).all()]
    session.commit()

    def count(status: ReconciliationStatus) -> int:
        return sum(1 for r in results if r.status == status)

    return ReconciliationRunOut(
        processed=len(results),
        matched=count(ReconciliationStatus.MATCHED),
        mismatched=count(ReconciliationStatus.MISMATCHED),
        failed=count(ReconciliationStatus.FAILED),
        results=results,
    )
