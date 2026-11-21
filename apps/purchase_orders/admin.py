from django.contrib import admin

from .models import PurchaseOrder


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = (
        "po_number", "vendor", "order_date", "total_amount", "remaining_balance", "status",
    )
    list_filter = ("status",)
    search_fields = ("po_number", "vendor__vendor_code", "vendor__name")
    readonly_fields = ("total_amount", "remaining_balance")
