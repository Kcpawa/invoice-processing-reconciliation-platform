from django.contrib import admin

from .models import UploadBatch, UploadRecord


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(UploadBatch)
class UploadBatchAdmin(ReadOnlyAdmin):
    list_display = (
        "id", "file_name", "status", "total_records", "matched_count",
        "mismatched_count", "duplicate_count", "failed_count", "created_at",
    )
    list_filter = ("status",)


@admin.register(UploadRecord)
class UploadRecordAdmin(ReadOnlyAdmin):
    list_display = ("batch", "row_number", "invoice_number", "vendor_code", "classification")
    list_filter = ("classification",)
    search_fields = ("invoice_number", "vendor_code", "po_number")
