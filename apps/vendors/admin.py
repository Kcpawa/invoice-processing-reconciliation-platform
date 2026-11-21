from django.contrib import admin

from .models import Vendor


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("vendor_code", "name", "email", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("vendor_code", "name", "email", "tax_id")
