from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, Query, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi_app.config import Settings, get_settings
from fastapi_app.database import get_db
from fastapi_app.models import UploadBatch, UploadRecord
from fastapi_app.schemas import (
    ErrorResponse,
    Page,
    UploadBatchOut,
    UploadRecordOut,
    UploadResultOut,
)
from fastapi_app.services import bulk_upload
from fastapi_app.services.reports import paginate, total_pages
from shared.enums import BatchStatus, RecordClassification

router = APIRouter(prefix="/api/v1/uploads", tags=["Bulk upload & history"])

DbSession = Annotated[Session, Depends(get_db)]
PageParam = Annotated[int, Query(ge=1, description="Page number (1-based).")]
PageSizeParam = Annotated[int, Query(ge=1, le=200, description="Items per page.")]


@router.post(
    "/invoices",
    response_model=UploadResultOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a CSV / XLSX file of invoices",
    responses={
        200: {"model": UploadResultOut, "description": "Idempotent replay of an earlier upload."},
        400: {"model": ErrorResponse, "description": "Unsupported, empty or malformed file."},
        409: {"model": ErrorResponse, "description": "Idempotency conflict / upload in progress."},
        413: {"model": ErrorResponse, "description": "File too large."},
    },
)
def upload_invoices(
    response: Response,
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File(description="A .csv or .xlsx file.")],
    idempotency_key: Annotated[
        str | None,
        Header(
            description="Optional client-generated key. Re-sending the same key and "
            "file returns the original result. Defaults to the file's SHA-256."
        ),
    ] = None,
    force: Annotated[
        bool,
        Query(
            description="Process the file again even if identical content was uploaded "
            "before (ignored when an Idempotency-Key header is sent). Already-imported "
            "rows are then reported as duplicates."
        ),
    ] = False,
):
    """Each row is validated and classified:

    | Classification | Meaning | Stored as invoice? |
    |---|---|---|
    | `MATCHED` | Agrees with its PO; PO balance is reduced | yes |
    | `MISMATCHED` | Valid, but wrong vendor for the PO / exceeds balance / PO closed | yes, for review |
    | `DUPLICATE` | Same vendor + invoice number already exists or repeats in the file | no |
    | `FAILED` | Missing/invalid fields, unknown or inactive vendor, unknown PO, bad dates/amounts | no |

    **Columns** (header row required, any order, case-insensitive):
    `invoice_number, vendor_code, po_number, invoice_date, due_date, amount`
    plus optional `tax_amount` (default 0) and `total_amount` (default amount + tax).
    """
    # Read one byte past the limit so oversized files are detected without
    # loading them fully into memory.
    content = file.file.read(settings.max_upload_size_bytes + 1)
    batch, replay = bulk_upload.process_upload(
        db,
        filename=file.filename,
        content=content,
        idempotency_key=idempotency_key,
        force=force,
        settings=settings,
    )
    valid, invalid = bulk_upload.split_records(bulk_upload.get_batch_records(db, batch.id))
    if replay:
        response.status_code = status.HTTP_200_OK
    return UploadResultOut(
        idempotent_replay=replay,
        summary=UploadBatchOut.model_validate(batch),
        valid_records=[UploadRecordOut.model_validate(r) for r in valid],
        invalid_records=[UploadRecordOut.model_validate(r) for r in invalid],
    )


@router.get("", response_model=Page[UploadBatchOut], summary="Upload / processing history")
def list_uploads(
    db: DbSession,
    page: PageParam = 1,
    page_size: PageSizeParam = 20,
    batch_status: Annotated[BatchStatus | None, Query(alias="status")] = None,
    file_name: Annotated[str | None, Query(description="Substring match.")] = None,
    created_from: Annotated[date | None, Query(description="Inclusive, UTC.")] = None,
    created_to: Annotated[date | None, Query(description="Inclusive, UTC.")] = None,
):
    stmt = select(UploadBatch).order_by(UploadBatch.id.desc())
    if batch_status:
        stmt = stmt.where(UploadBatch.status == batch_status.value)
    if file_name:
        stmt = stmt.where(UploadBatch.file_name.ilike(f"%{file_name.strip()}%"))
    if created_from:
        stmt = stmt.where(UploadBatch.created_at >= bulk_upload.parse_date_bound(created_from))
    if created_to:
        stmt = stmt.where(
            UploadBatch.created_at < bulk_upload.parse_date_bound(created_to, end=True)
        )
    count, rows = paginate(db, stmt, page, page_size)
    return Page[UploadBatchOut](
        count=count, page=page, page_size=page_size,
        total_pages=total_pages(count, page_size),
        results=[UploadBatchOut.model_validate(row[0]) for row in rows],
    )


@router.get(
    "/{batch_id}",
    response_model=UploadBatchOut,
    summary="Processing summary of one upload",
    responses={404: {"model": ErrorResponse}},
)
def get_upload(batch_id: int, db: DbSession):
    return UploadBatchOut.model_validate(bulk_upload.get_batch(db, batch_id))


@router.get(
    "/{batch_id}/records",
    response_model=Page[UploadRecordOut],
    summary="Row-level results of one upload",
    responses={404: {"model": ErrorResponse}},
)
def list_upload_records(
    batch_id: int,
    db: DbSession,
    page: PageParam = 1,
    page_size: PageSizeParam = 50,
    classification: RecordClassification | None = None,
):
    bulk_upload.get_batch(db, batch_id)  # 404 if unknown
    stmt = (
        select(UploadRecord)
        .where(UploadRecord.batch_id == batch_id)
        .order_by(UploadRecord.row_number)
    )
    if classification:
        stmt = stmt.where(UploadRecord.classification == classification.value)
    count, rows = paginate(db, stmt, page, page_size)
    return Page[UploadRecordOut](
        count=count, page=page, page_size=page_size,
        total_pages=total_pages(count, page_size),
        results=[UploadRecordOut.model_validate(row[0]) for row in rows],
    )
