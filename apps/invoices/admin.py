from django.contrib import admin

from .models import Invoice


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Read-only: edits must go through the API so PO balances stay correct."""

    list_display = (
        "invoice_number", "vendor", "purchase_order", "invoice_date",
        "total_amount", "reconciliation_status", "source",
    )
    list_filter = ("reconciliation_status", "source")
    search_fields = ("invoice_number", "vendor__vendor_code", "purchase_order__po_number")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
