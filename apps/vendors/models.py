from django.db import models

from apps.common.models import TimeStampedModel
from shared.enums import VendorStatus


class Vendor(TimeStampedModel):
    vendor_code = models.CharField(
        max_length=30, unique=True, help_text="Unique business identifier, e.g. VEND-001."
    )
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=254, blank=True, default="")
    phone = models.CharField(max_length=30, blank=True, default="")
    tax_id = models.CharField(
        max_length=50, unique=True, null=True, blank=True,
        help_text="Tax registration number (GSTIN / VAT / EIN). Unique when present.",
    )
    address = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=10, choices=VendorStatus.choices(), default=VendorStatus.ACTIVE.value
    )

    class Meta:
        db_table = "vendors"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["status"], name="idx_vendor_status"),
            models.Index(fields=["name"], name="idx_vendor_name"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=VendorStatus.values()),
                name="ck_vendor_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor_code} - {self.name}"

    @property
    def is_active(self) -> bool:
        return self.status == VendorStatus.ACTIVE.value
