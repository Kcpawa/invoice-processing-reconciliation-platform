"""Idempotency and failure handling of bulk uploads."""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from fastapi_app.models import Invoice, UploadBatch, UploadRecord, utcnow
from fastapi_app.services import bulk_upload
from tests.helpers import invoice_row, make_csv


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


@pytest.fixture
def po(make_vendor, make_po):
    return make_po(make_vendor("V001"), "PO-001", amount="10000.00")


@pytest.fixture
def content():
    return make_csv([invoice_row("INV-1"), invoice_row("INV-2")])


def test_same_file_twice_is_replayed_not_reprocessed(upload, sa_session, po, content):
    first = upload(content)
    second = upload(content)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["idempotent_replay"] is True
    assert second.json()["summary"] == first.json()["summary"]
    assert second.json()["valid_records"] == first.json()["valid_records"]

    assert count(sa_session, UploadBatch) == 1
    assert count(sa_session, Invoice) == 2
    sa_session.refresh(po)
    assert po.remaining_balance == Decimal("7640.00")  # deducted once


def test_default_key_is_the_content_hash(upload, po, content):
    summary = upload(content, filename="first.csv").json()["summary"]
    assert summary["idempotency_key"] == f"sha256:{summary['file_hash']}"
    # Same bytes under another file name is still the same upload.
    assert upload(content, filename="renamed.csv").json()["idempotent_replay"] is True


def test_same_idempotency_key_is_replayed(upload, sa_session, po, content):
    first = upload(content, key="req-123")
    second = upload(content, key="req-123")

    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["summary"]["id"] == first.json()["summary"]["id"]
    assert second.json()["summary"]["idempotency_key"] == "req-123"
    assert count(sa_session, Invoice) == 2


def test_idempotency_key_reused_with_different_file_is_rejected(upload, sa_session, po, content):
    upload(content, key="req-123")
    response = upload(make_csv([invoice_row("INV-9")]), key="req-123")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_key_reused"
    assert count(sa_session, Invoice) == 2


def test_new_key_reprocesses_file_and_rows_become_duplicates(upload, sa_session, po, content):
    upload(content, key="req-1")
    response = upload(content, key="req-2")

    assert response.status_code == 201
    summary = response.json()["summary"]
    assert (summary["success_count"], summary["duplicate_count"]) == (0, 2)
    assert count(sa_session, UploadBatch) == 2
    assert count(sa_session, Invoice) == 2
    sa_session.refresh(po)
    assert po.remaining_balance == Decimal("7640.00")  # row-level dedupe protects the balance


def test_force_reprocesses_identical_content(upload, sa_session, make_vendor, make_po):
    """Typical use: rows failed because master data was missing; fix, re-upload."""
    content = make_csv([invoice_row("INV-1")])
    first = upload(content).json()
    assert first["invalid_records"][0]["errors"][0]["code"] == "VENDOR_NOT_FOUND"

    make_po(make_vendor("V001"), "PO-001")
    assert upload(content).json()["idempotent_replay"] is True  # still the stored result

    forced = upload(content, force=True)
    assert forced.status_code == 201
    assert forced.json()["summary"]["matched_count"] == 1
    assert forced.json()["summary"]["idempotency_key"] is None
    assert count(sa_session, UploadBatch) == 2


def test_overlong_idempotency_key_is_rejected(upload, po, content):
    response = upload(content, key="k" * 256)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_idempotency_key"


def test_upload_in_progress_is_a_conflict(upload, sa_session, po, content):
    sa_session.add(UploadBatch(idempotency_key="req-1", file_name="x.csv", file_type="csv",
                               file_hash=bulk_upload.hashlib.sha256(content).hexdigest(),
                               status="PROCESSING"))
    sa_session.commit()

    response = upload(content, key="req-1")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "upload_in_progress"
    assert count(sa_session, Invoice) == 0


def test_abandoned_processing_batch_can_be_retried(upload, sa_session, po, content):
    stale = UploadBatch(idempotency_key="req-1", file_name="x.csv", file_type="csv",
                        file_hash=bulk_upload.hashlib.sha256(content).hexdigest(),
                        status="PROCESSING", created_at=utcnow() - timedelta(hours=1))
    sa_session.add(stale)
    sa_session.commit()

    response = upload(content, key="req-1")

    assert response.status_code == 201
    assert response.json()["summary"]["id"] == stale.id
    assert response.json()["summary"]["status"] == "COMPLETED"
    assert count(sa_session, UploadBatch) == 1


def test_crash_rolls_back_everything_and_retry_succeeds(
    upload, sa_session, po, content, monkeypatch
):
    real_import = bulk_upload._import_invoice
    calls = {"n": 0}

    def explode_on_second_row(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash")
        return real_import(*args, **kwargs)

    monkeypatch.setattr(bulk_upload, "_import_invoice", explode_on_second_row)
    failed = upload(content, key="req-1")

    assert failed.status_code == 500
    error = failed.json()["error"]
    assert error["code"] == "processing_failed"
    batch = sa_session.get(UploadBatch, error["details"]["batch_id"])
    sa_session.refresh(batch)
    assert batch.status == "FAILED"
    assert "simulated crash" in batch.error_message
    # The first row had been inserted before the crash - it must be gone.
    assert count(sa_session, Invoice) == 0
    assert count(sa_session, UploadRecord) == 0
    sa_session.refresh(po)
    assert po.remaining_balance == Decimal("10000.00")

    monkeypatch.setattr(bulk_upload, "_import_invoice", real_import)
    retry = upload(content, key="req-1")

    assert retry.status_code == 201
    assert retry.json()["summary"]["id"] == batch.id
    assert retry.json()["summary"]["matched_count"] == 2
    assert retry.json()["summary"]["error_message"] == ""
    assert count(sa_session, UploadBatch) == 1
    sa_session.refresh(po)
    assert po.remaining_balance == Decimal("7640.00")


def test_row_rejected_by_database_does_not_abort_the_batch(
    upload, sa_session, po, monkeypatch
):
    """Simulates a concurrent insert that slips past the duplicate pre-check:
    the unique constraint fires inside the row's SAVEPOINT."""
    real_process = bulk_upload._process_rows

    def insert_competitor_then_process(session, batch, rows, today):
        # Runs after the pre-check query would be issued by a concurrent request.
        result_holder = {}

        real_evaluate = bulk_upload.evaluate

        def evaluate_then_race(candidate, vendor, purchase_order, **kwargs):
            result = real_evaluate(candidate, vendor, purchase_order, **kwargs)
            if candidate.invoice_number == "INV-2" and "done" not in result_holder:
                result_holder["done"] = True
                session.add(Invoice(
                    invoice_number="INV-2", vendor_id=vendor.id,
                    purchase_order_id=purchase_order.id,
                    invoice_date=candidate.invoice_date, due_date=candidate.due_date,
                    amount=candidate.amount, tax_amount=candidate.tax_amount,
                    total_amount=candidate.total_amount,
                ))
                session.flush()
            return result

        monkeypatch.setattr(bulk_upload, "evaluate", evaluate_then_race)
        return real_process(session, batch, rows, today)

    monkeypatch.setattr(bulk_upload, "_process_rows", insert_competitor_then_process)

    response = upload(make_csv([invoice_row("INV-1"), invoice_row("INV-2"), invoice_row("INV-3")]))

    assert response.status_code == 201
    body = response.json()
    classes = {r["row_number"]: r["classification"]
               for r in body["valid_records"] + body["invalid_records"]}
    assert classes == {2: "MATCHED", 3: "DUPLICATE", 4: "MATCHED"}
    sa_session.refresh(po)
    # Only the two imported rows consumed balance; the rolled-back row did not.
    assert po.remaining_balance == Decimal("7640.00")
