from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from shared.rules import normalize_code

from .models import Vendor


class VendorSerializer(serializers.ModelSerializer):
    vendor_code = serializers.CharField(max_length=30)
    tax_id = serializers.CharField(
        max_length=50, required=False, allow_null=True, allow_blank=True
    )

    class Meta:
        model = Vendor
        fields = [
            "id", "vendor_code", "name", "email", "phone", "tax_id",
            "address", "status", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def _ensure_unique(self, field: str, value: str, message: str) -> None:
        UniqueValidator(queryset=Vendor.objects.all(), message=message)(
            value, self.fields[field]
        )

    def validate_vendor_code(self, value: str) -> str:
        value = normalize_code(value)
        if not value:
            raise serializers.ValidationError("Vendor code cannot be blank.")
        self._ensure_unique(
            "vendor_code", value, "A vendor with this vendor code already exists."
        )
        return value

    def validate_tax_id(self, value):
        if not value or not value.strip():
            return None  # store NULL so the unique constraint ignores it
        value = normalize_code(value)
        self._ensure_unique("tax_id", value, "A vendor with this tax ID already exists.")
        return value

    def validate_name(self, value: str) -> str:
        return value.strip()
