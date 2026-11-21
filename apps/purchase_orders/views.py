from django.db import transaction
from rest_framework import viewsets

from .filters import PurchaseOrderFilter
from .models import PurchaseOrder
from .serializers import PurchaseOrderSerializer


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    """Purchase orders.

    `total_amount` and `remaining_balance` are maintained by the system:
    total = amount + tax, and the balance is reduced when an invoice is
    reconciled as MATCHED. A PO referenced by invoices cannot be deleted (409).
    """

    serializer_class = PurchaseOrderSerializer
    filterset_class = PurchaseOrderFilter
    search_fields = ["po_number", "description", "vendor__vendor_code", "vendor__name"]
    ordering_fields = [
        "id", "po_number", "order_date", "total_amount", "remaining_balance", "created_at",
    ]
    ordering = ["id"]

    def get_queryset(self):
        queryset = PurchaseOrder.objects.select_related("vendor")
        # Lock the row while it is being changed so a concurrent reconciliation
        # (which also locks the PO) cannot interleave with a balance recompute.
        if self.request.method in ("PUT", "PATCH", "DELETE"):
            queryset = queryset.select_for_update(of=("self",))
        return queryset

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)
