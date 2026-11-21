import django_filters

from shared.enums import POStatus

from .models import PurchaseOrder


class PurchaseOrderFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=POStatus.choices())
    vendor = django_filters.NumberFilter(field_name="vendor_id")
    vendor_code = django_filters.CharFilter(
        field_name="vendor__vendor_code", lookup_expr="iexact"
    )
    po_number = django_filters.CharFilter(lookup_expr="iexact")
    order_date_from = django_filters.DateFilter(field_name="order_date", lookup_expr="gte")
    order_date_to = django_filters.DateFilter(field_name="order_date", lookup_expr="lte")
    min_remaining_balance = django_filters.NumberFilter(
        field_name="remaining_balance", lookup_expr="gte"
    )
    max_remaining_balance = django_filters.NumberFilter(
        field_name="remaining_balance", lookup_expr="lte"
    )

    class Meta:
        model = PurchaseOrder
        fields = ["status", "vendor", "vendor_code", "po_number"]
