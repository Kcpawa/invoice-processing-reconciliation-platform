from django.db import models
from django.db.models import F, Q

from apps.common.models import TimeStampedModel
from shared.enums import InvoiceSource, ReconciliationStatus


class Invoice(TimeStampedModel):
    invoice_number = models.CharField(max_length=50)
    vendor = models.ForeignKey(
        "vendors.Vendor", on_delete=models.PROTECT, related_name="invoices"
    )
    purchase_order = models.ForeignKey(
        "purchase_orders.PurchaseOrder", on_delete=models.PROTECT, related_name="invoices"
    )
    invoice_date = models.DateField()
    due_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2, help_text="Pre-tax amount.")
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    reconciliation_status = models.CharField(
        max_length=12,
        choices=ReconciliationStatus.choices(),
        default=ReconciliationStatus.PENDING.value,
    )
    reconciliation_notes = models.JSONField(
        default=list, blank=True,
        help_text="List of {code, message} reasons from the last reconciliation run.",
    )
    reconciled_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(
        max_length=12, choices=InvoiceSource.choices(), default=InvoiceSource.API.value
    )

    class Meta:
        db_table = "invoices"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["reconciliation_status"], name="idx_invoice_recon_status"),
            models.Index(
                fields=["purchase_order", "reconciliation_status"], name="idx_invoice_po_status"
            ),
            models.Index(fields=["invoice_date"], name="idx_invoice_date"),
            models.Index(fields=["due_date"], name="idx_invoice_due_date"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "invoice_number"], name="uq_invoice_vendor_number"
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name="ck_invoice_amount_positive"),
            models.CheckConstraint(
                condition=Q(tax_amount__gte=0), name="ck_invoice_tax_non_negative"
            ),
            models.CheckConstraint(
                condition=Q(total_amount__gt=0), name="ck_invoice_total_positive"
            ),
            models.CheckConstraint(
                condition=Q(due_date__gte=F("invoice_date")),
                name="ck_invoice_due_not_before_invoice_date",
            ),
            models.CheckConstraint(
                condition=Q(reconciliation_status__in=ReconciliationStatus.values()),
                name="ck_invoice_recon_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.invoice_number
