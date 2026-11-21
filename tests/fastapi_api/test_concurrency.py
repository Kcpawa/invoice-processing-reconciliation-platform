"""Race conditions - these need real row locks, so they only run on PostgreSQL
(TEST_USE_POSTGRES=1). SQLite serialises writers and would prove nothing."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from fastapi_app.config import Settings
from fastapi_app.exceptions import ConflictError
from fastapi_app.models import Invoice, PurchaseOrder, UploadBatch
from fastapi_app.services import bulk_upload, reconciliation
from tests.conftest import USE_POSTGRES
from tests.helpers import invoice_row, make_csv

pytestmark = pytest.mark.skipif(not USE_POSTGRES, reason="requires PostgreSQL row locking")

WORKERS = 6


def run_concurrently(engine, work, count=WORKERS):
    """Run work(session, index) in `count` threads released at the same instant."""
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    barrier = Barrier(count)

    def worker(index):
        with factory() as session:
            barrier.wait()
            try:
                return work(session, index)
            except ConflictError as exc:
                session.rollback()
                return exc

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(worker, range(count)))


def test_concurrent_reconciliations_never_overdraw_a_po(
    engine, sa_session, make_vendor, make_po, make_invoice
):
    vendor = make_vendor("V001")
    po = make_po(vendor, "PO-001", amount="2500.00")  # room for two invoices of 1,000
    invoices = [make_invoice(vendor, po, f"INV-{i}", amount="1000.00") for i in range(WORKERS)]

    results = run_concurrently(
        engine, lambda session, i: reconciliation.reconcile_one(session, invoices[i].id)
    )

    statuses = sorted(r.status.value for r in results)
    assert statuses == ["MATCHED"] * 2 + ["MISMATCHED"] * (WORKERS - 2)
    sa_session.expire_all()
    assert sa_session.get(PurchaseOrder, po.id).remaining_balance == Decimal("500.00")


def test_concurrent_identical_uploads_are_processed_exactly_once(
    engine, sa_session, make_vendor, make_po
):
    po = make_po(make_vendor("V001"), "PO-001", amount="10000.00")
    content = make_csv([invoice_row(f"INV-{i}") for i in range(5)])
    settings = Settings(_env_file=None)

    def upload(session, _index):
        return bulk_upload.process_upload(
            session, filename="invoices.csv", content=content, idempotency_key="same-key",
            force=False, settings=settings,
        )

    results = run_concurrently(engine, upload)

    processed = [r for r in results if not isinstance(r, Exception) and r[1] is False]
    others = [r for r in results if r not in processed]
    assert len(processed) == 1
    # Everyone else either got the stored result or was told it is in progress.
    assert all(isinstance(r, ConflictError) or r[1] is True for r in others)

    sa_session.expire_all()
    assert sa_session.scalar(select(func.count()).select_from(UploadBatch)) == 1
    assert sa_session.scalar(select(func.count()).select_from(Invoice)) == 5
    assert sa_session.get(PurchaseOrder, po.id).remaining_balance == Decimal("4100.00")


def test_concurrent_different_files_with_the_same_invoices_import_each_once(
    engine, sa_session, make_vendor, make_po
):
    """Different idempotency keys, overlapping invoice numbers: the PO lock
    serialises the batches, so later ones see the rows as duplicates."""
    po = make_po(make_vendor("V001"), "PO-001", amount="10000.00")
    content = make_csv([invoice_row("INV-1"), invoice_row("INV-2")])
    settings = Settings(_env_file=None)

    def upload(session, index):
        batch, _ = bulk_upload.process_upload(
            session, filename="invoices.csv", content=content,
            idempotency_key=f"key-{index}", force=False, settings=settings,
        )
        return batch.matched_count, batch.duplicate_count

    results = run_concurrently(engine, upload)

    assert sorted(results) == [(0, 2)] * (WORKERS - 1) + [(2, 0)]
    sa_session.expire_all()
    assert sa_session.scalar(select(func.count()).select_from(Invoice)) == 2
    assert sa_session.get(PurchaseOrder, po.id).remaining_balance == Decimal("7640.00")
