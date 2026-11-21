from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from fastapi_app.database import get_db
from fastapi_app.schemas import (
    Page,
    POBalanceReport,
    ProcessingStatsOut,
    VendorSummaryOut,
)
from fastapi_app.services import reports
from shared.enums import POStatus, VendorStatus

router = APIRouter(prefix="/api/v1/reports", tags=["Reports"])

DbSession = Annotated[Session, Depends(get_db)]
PageParam = Annotated[int, Query(ge=1, description="Page number (1-based).")]
PageSizeParam = Annotated[int, Query(ge=1, le=200, description="Items per page.")]


@router.get(
    "/vendor-summary",
    response_model=Page[VendorSummaryOut],
    summary="Vendor-wise invoice summary",
)
def vendor_summary(
    db: DbSession,
    page: PageParam = 1,
    page_size: PageSizeParam = 20,
    vendor_code: str | None = None,
    vendor_status: VendorStatus | None = None,
    search: Annotated[str | None, Query(description="Vendor name contains.")] = None,
    invoice_date_from: date | None = None,
    invoice_date_to: date | None = None,
):
    """Invoice counts and amounts per vendor, broken down by reconciliation
    status. Vendors with no invoices in the period are listed with zeros."""
    return reports.vendor_invoice_summary(
        db,
        vendor_code=vendor_code,
        vendor_status=vendor_status.value if vendor_status else None,
        search=search,
        invoice_date_from=invoice_date_from,
        invoice_date_to=invoice_date_to,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/po-balance",
    response_model=POBalanceReport,
    summary="PO-wise remaining balance report",
)
def po_balance(
    db: DbSession,
    page: PageParam = 1,
    page_size: PageSizeParam = 20,
    vendor_code: str | None = None,
    po_number: str | None = None,
    po_status: Annotated[POStatus | None, Query(alias="status")] = None,
    has_balance: Annotated[
        bool | None, Query(description="true = balance left, false = fully invoiced.")
    ] = None,
    order_date_from: date | None = None,
    order_date_to: date | None = None,
):
    """Total, invoiced and remaining amount per purchase order, plus grand
    totals across all POs matching the filters."""
    return reports.po_balance_report(
        db,
        vendor_code=vendor_code,
        po_number=po_number,
        status=po_status.value if po_status else None,
        has_balance=has_balance,
        order_date_from=order_date_from,
        order_date_to=order_date_to,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/processing-stats",
    response_model=ProcessingStatsOut,
    summary="Invoice processing statistics",
)
def processing_stats(
    db: DbSession,
    date_from: Annotated[date | None, Query(description="Created on/after (UTC).")] = None,
    date_to: Annotated[date | None, Query(description="Created on/before (UTC).")] = None,
):
    return reports.processing_statistics(db, date_from=date_from, date_to=date_to)
