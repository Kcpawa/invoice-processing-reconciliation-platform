from decimal import Decimal

from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from apps.vendors.models import Vendor
from shared.enums import POStatus
from shared.rules import normalize_code

from .models import PurchaseOrder


class PurchaseOrderSerializer(serializers.ModelSerializer):
    po_number = serializers.CharField(max_length=50)
    vendor = serializers.PrimaryKeyRelatedField(queryset=Vendor.objects.all())
    vendor_code = serializers.CharField(source="vendor.vendor_code", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    tax_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False
    )
    invoiced_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True
    )

    class Meta:
        model = PurchaseOrder
        fields = [
            "id", "po_number", "vendor", "vendor_code", "vendor_name", "description",
            "order_date", "amount", "tax_amount", "total_amount", "remaining_balance",
            "invoiced_amount", "status", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "total_amount", "remaining_balance", "created_at", "updated_at",
        ]

    def validate_po_number(self, value: str) -> str:
        value = normalize_code(value)
        if not value:
            raise serializers.ValidationError("PO number cannot be blank.")
        UniqueValidator(
            queryset=PurchaseOrder.objects.all(),
            message="A purchase order with this PO number already exists.",
        )(value, self.fields["po_number"])
        return value

    def validate(self, attrs):
        instance = self.instance
        vendor = attrs.get("vendor")

        if instance is None:
            if not vendor.is_active:
                raise serializers.ValidationError(
                    {"vendor": "Purchase orders can only be raised for active vendors."}
                )
        elif vendor is not None and vendor.pk != instance.vendor_id:
            if instance.invoices.exists():
                raise serializers.ValidationError(
                    {"vendor": "Vendor cannot be changed once invoices reference this PO."}
                )
            if not vendor.is_active:
                raise serializers.ValidationError(
                    {"vendor": "Purchase orders can only be raised for active vendors."}
                )

        amount = attrs.get("amount", getattr(instance, "amount", None))
        tax = attrs.get("tax_amount", getattr(instance, "tax_amount", Decimal("0")))
        total = amount + tax
        consumed = instance.invoiced_amount if instance else Decimal("0")
        if total < consumed:
            raise serializers.ValidationError(
                {
                    "amount": (
                        f"New total {total} is less than the amount already "
                        f"invoiced against this PO ({consumed})."
                    )
                }
            )
        attrs["tax_amount"] = tax
        attrs["total_amount"] = total
        attrs["remaining_balance"] = total - consumed

        # Keep status consistent with the balance unless the PO is cancelled.
        status = attrs.get("status", getattr(instance, "status", POStatus.OPEN.value))
        if status == POStatus.OPEN.value and consumed > 0 and total == consumed:
            attrs["status"] = POStatus.CLOSED.value
        return attrs
