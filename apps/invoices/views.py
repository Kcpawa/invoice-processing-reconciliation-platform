from django.db import transaction
from rest_framework import viewsets

from shared.enums import ReconciliationStatus

from . import services
from .filters import InvoiceFilter
from .models import Invoice
from .serializers import InvoiceSerializer


class InvoiceViewSet(viewsets.ModelViewSet):
    """Vendor invoices.

    New invoices start as PENDING and are classified by the reconciliation
    engine (FastAPI service: `POST /api/v1/reconciliation/run`).

    Editing a reconciliation-relevant field resets the invoice to PENDING;
    if it was MATCHED, the PO balance it consumed is released. Deleting a
    MATCHED invoice releases its balance too.
    """

    serializer_class = InvoiceSerializer
    filterset_class = InvoiceFilter
    search_fields = ["invoice_number", "vendor__vendor_code", "vendor__name",
                     "purchase_order__po_number"]
    ordering_fields = ["id", "invoice_number", "invoice_date", "due_date",
                       "total_amount", "created_at"]
    ordering = ["id"]

    def get_queryset(self):
        queryset = Invoice.objects.select_related("vendor", "purchase_order")
        if self.request.method in ("PUT", "PATCH", "DELETE"):
            queryset = queryset.select_for_update(of=("self",))
        return queryset

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        invoice = serializer.instance
        changed = any(
            field in serializer.validated_data
            and serializer.validated_data[field] != getattr(invoice, field)
            for field in services.RECONCILIATION_RELEVANT_FIELDS
        )
        if not changed:
            serializer.save()
            return
        services.release_po_balance(invoice)  # uses the pre-update PO and total
        serializer.save(
            reconciliation_status=ReconciliationStatus.PENDING.value,
            reconciliation_notes=[],
            reconciled_at=None,
        )

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        services.delete_invoice(instance)
