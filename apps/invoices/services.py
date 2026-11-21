"""Invoice-side bookkeeping done by the Django service.

Reconciliation itself (classifying an invoice and consuming PO balance) is
owned by the FastAPI service. What Django must guarantee is the inverse:
when a MATCHED invoice is edited or deleted, the balance it consumed is
handed back to its purchase order.
"""
from django.db import transaction

from apps.purchase_orders.models import PurchaseOrder
from shared.enums import POStatus, ReconciliationStatus

from .models import Invoice

# Changing any of these invalidates a previous reconciliation result.
RECONCILIATION_RELEVANT_FIELDS = (
    "invoice_number", "vendor", "purchase_order", "invoice_date", "due_date",
    "amount", "tax_amount", "total_amount",
)


def release_po_balance(invoice: Invoice) -> None:
    """Return a MATCHED invoice's total to its PO. Must run inside a transaction."""
    if invoice.reconciliation_status != ReconciliationStatus.MATCHED.value:
        return
    po = PurchaseOrder.objects.select_for_update().get(pk=invoice.purchase_order_id)
    po.remaining_balance = min(po.total_amount, po.remaining_balance + invoice.total_amount)
    if po.status == POStatus.CLOSED.value and po.remaining_balance > 0:
        po.status = POStatus.OPEN.value
    po.save(update_fields=["remaining_balance", "status", "updated_at"])


@transaction.atomic
def delete_invoice(invoice: Invoice) -> None:
    release_po_balance(invoice)
    invoice.delete()
