import django_filters

from shared.enums import VendorStatus

from .models import Vendor


class VendorFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=VendorStatus.choices())
    vendor_code = django_filters.CharFilter(lookup_expr="iexact")
    name = django_filters.CharFilter(lookup_expr="icontains")
    created_after = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        model = Vendor
        fields = ["status", "vendor_code", "name"]
