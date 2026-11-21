from datetime import date
from decimal import Decimal

from rest_framework import serializers

from apps.purchase_orders.models import PurchaseOrder
from apps.vendors.models import Vendor
from shared.rules import AMOUNT_TOLERANCE, normalize_code

from .models import Invoice


class InvoiceSerializer(serializers.ModelSerializer):
    """`total_amount` is optional on input: it is derived as amount + tax, and
    rejected if a supplied value disagrees.

    Whether the invoice agrees with its PO (vendor match, available balance)
    is deliberately *not* validated here - that is the reconciliation
    engine's job, and such invoices are stored and classified MISMATCHED.
    """

    invoice_number = serializers.CharField(max_length=50)
    vendor = serializers.PrimaryKeyRelatedField(queryset=Vendor.objects.all())
    vendor_code = serializers.CharField(source="vendor.vendor_code", read_only=True)
    purchase_order = serializers.PrimaryKeyRelatedField(queryset=PurchaseOrder.objects.all())
    po_number = serializers.CharField(source="purchase_order.po_number", read_only=True)
    amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    tax_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False
    )
    total_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0.01"), required=False
    )

    class Meta:
        model = Invoice
        # Uniqueness of (vendor, invoice_number) is checked in validate(), after
        # the number is normalised, so the error is reported on the field.
        validators = []
        fields = [
            "id", "invoice_number", "vendor", "vendor_code", "purchase_order", "po_number",
            "invoice_date", "due_date", "amount", "tax_amount", "total_amount",
            "reconciliation_status", "reconciliation_notes", "reconciled_at", "source",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "reconciliation_status", "reconciliation_notes", "reconciled_at",
            "source", "created_at", "updated_at",
        ]

    def validate_invoice_number(self, value: str) -> str:
        value = normalize_code(value)
        if not value:
            raise serializers.ValidationError("Invoice number cannot be blank.")
        return value

    def validate_invoice_date(self, value: date) -> date:
        if value > date.today():
            raise serializers.ValidationError("Invoice date cannot be in the future.")
        return value

    def validate(self, attrs):
        instance = self.instance

        def current(field, default=None):
            return attrs.get(field, getattr(instance, field, default))

        if current("due_date") < current("invoice_date"):
            raise serializers.ValidationError(
                {"due_date": "Due date cannot be before the invoice date."}
            )

        amount = current("amount")
        tax = current("tax_amount", Decimal("0"))
        expected_total = amount + tax
        supplied_total = attrs.get("total_amount")
        if supplied_total is not None and abs(supplied_total - expected_total) > AMOUNT_TOLERANCE:
            raise serializers.ValidationError(
                {"total_amount": f"Total must equal amount + tax ({expected_total})."}
            )
        attrs["tax_amount"] = tax
        attrs["total_amount"] = expected_total

        duplicates = Invoice.objects.filter(
            vendor=current("vendor"), invoice_number=current("invoice_number")
        )
        if instance is not None:
            duplicates = duplicates.exclude(pk=instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError(
                {"invoice_number": "This vendor already has an invoice with this number."}
            )
        return attrs
