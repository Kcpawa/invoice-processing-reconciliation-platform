from django.db import models
from django.db.models import F, Q

from apps.common.models import TimeStampedModel
from shared.enums import POStatus


class PurchaseOrder(TimeStampedModel):
    po_number = models.CharField(max_length=50, unique=True)
    vendor = models.ForeignKey(
        "vendors.Vendor", on_delete=models.PROTECT, related_name="purchase_orders"
    )
    description = models.CharField(max_length=500, blank=True, default="")
    order_date = models.DateField()
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, help_text="Pre-tax amount."
    )
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_amount = models.DecimalField(
        max_digits=14, decimal_places=2, help_text="amount + tax_amount."
    )
    remaining_balance = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="total_amount minus the total of all MATCHED invoices.",
    )
    status = models.CharField(
        max_length=10, choices=POStatus.choices(), default=POStatus.OPEN.value
    )

    class Meta:
        db_table = "purchase_orders"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["vendor", "status"], name="idx_po_vendor_status"),
            models.Index(fields=["status"], name="idx_po_status"),
            models.Index(fields=["order_date"], name="idx_po_order_date"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="ck_po_amount_positive"),
            models.CheckConstraint(condition=Q(tax_amount__gte=0), name="ck_po_tax_non_negative"),
            models.CheckConstraint(
                condition=Q(total_amount=F("amount") + F("tax_amount")),
                name="ck_po_total_is_amount_plus_tax",
            ),
            models.CheckConstraint(
                condition=Q(remaining_balance__gte=0)
                & Q(remaining_balance__lte=F("total_amount")),
                name="ck_po_balance_within_total",
            ),
            models.CheckConstraint(
                condition=Q(status__in=POStatus.values()), name="ck_po_status_valid"
            ),
        ]

    def __str__(self) -> str:
        return self.po_number

    @property
    def invoiced_amount(self):
        return self.total_amount - self.remaining_balance
