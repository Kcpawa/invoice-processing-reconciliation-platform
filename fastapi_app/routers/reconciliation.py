from typing import Annotated

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from fastapi_app.database import get_db
from fastapi_app.schemas import (
    ErrorResponse,
    InvoiceReconciliationOut,
    ReconciliationRunOut,
    ReconciliationRunRequest,
)
from fastapi_app.services import reconciliation

router = APIRouter(prefix="/api/v1/reconciliation", tags=["Reconciliation"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post(
    "/run",
    response_model=ReconciliationRunOut,
    summary="Reconcile all open invoices",
)
def run_reconciliation(
    db: DbSession,
    params: Annotated[ReconciliationRunRequest | None, Body()] = None,
):
    """Classifies every `PENDING` and `MISMATCHED` invoice (e.g. invoices
    created through the Django CRUD API, or mismatches whose PO has since
    been corrected). `MATCHED` invoices reduce their PO's remaining balance.

    Safe to call repeatedly: already-matched invoices are never re-processed.
    """
    return reconciliation.run_reconciliation(db, params or ReconciliationRunRequest())


@router.post(
    "/invoices/{invoice_id}",
    response_model=InvoiceReconciliationOut,
    summary="Reconcile a single invoice",
    responses={404: {"model": ErrorResponse}},
)
def reconcile_invoice(invoice_id: int, db: DbSession):
    """Re-evaluates one invoice against its vendor and purchase order.
    Returns the current result unchanged if the invoice is already `MATCHED`."""
    return reconciliation.reconcile_one(db, invoice_id)
