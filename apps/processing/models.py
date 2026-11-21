"""Bulk-upload history tables.

Django owns the schema (migrations) for every table in the platform; these
two are read and written at runtime by the FastAPI service via SQLAlchemy.
"""
from django.db import models
from django.db.models import Q

from shared.enums import BatchStatus, RecordClassification


class UploadBatch(models.Model):
    idempotency_key = models.CharField(
        max_length=255, unique=True, null=True, blank=True,
        help_text="Client Idempotency-Key header, or 'sha256:<file hash>' by default.",
    )
    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=10)
    file_hash = models.CharField(max_length=64, help_text="SHA-256 of the file content.")
    file_size = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=12, choices=BatchStatus.choices(), default=BatchStatus.PROCESSING.value
    )
    total_records = models.PositiveIntegerField(default=0)
    matched_count = models.PositiveIntegerField(default=0)
    mismatched_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "upload_batches"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["status"], name="idx_batch_status"),
            models.Index(fields=["created_at"], name="idx_batch_created_at"),
            models.Index(fields=["file_hash"], name="idx_batch_file_hash"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=BatchStatus.values()), name="ck_batch_status_valid"
            ),
        ]

    def __str__(self) -> str:
        return f"Batch {self.pk} ({self.file_name})"


class UploadRecord(models.Model):
    """One row of an uploaded file and what happened to it."""

    batch = models.ForeignKey(UploadBatch, on_delete=models.CASCADE, related_name="records")
    row_number = models.PositiveIntegerField(help_text="Row number in the uploaded file.")
    invoice_number = models.CharField(max_length=50, blank=True, default="")
    vendor_code = models.CharField(max_length=30, blank=True, default="")
    po_number = models.CharField(max_length=50, blank=True, default="")
    classification = models.CharField(max_length=12, choices=RecordClassification.choices())
    errors = models.JSONField(default=list, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    invoice = models.ForeignKey(
        "invoices.Invoice", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="upload_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "upload_records"
        ordering = ["batch_id", "row_number"]
        indexes = [
            models.Index(fields=["batch", "classification"], name="idx_record_batch_class"),
            models.Index(fields=["classification"], name="idx_record_classification"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["batch", "row_number"], name="uq_record_batch_row"),
            models.CheckConstraint(
                condition=Q(classification__in=RecordClassification.values()),
                name="ck_record_classification_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"Batch {self.batch_id} row {self.row_number}: {self.classification}"
