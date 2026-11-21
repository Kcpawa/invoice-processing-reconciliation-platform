import django_filters

from shared.enums import InvoiceSource, ReconciliationStatus

from .models import Invoice


class InvoiceFilter(django_filters.FilterSet):
    reconciliation_status = django_filters.ChoiceFilter(choices=ReconciliationStatus.choices())
    source = django_filters.ChoiceFilter(choices=InvoiceSource.choices())
    vendor = django_filters.NumberFilter(field_name="vendor_id")
    vendor_code = django_filters.CharFilter(
        field_name="vendor__vendor_code", lookup_expr="iexact"
    )
    purchase_order = django_filters.NumberFilter(field_name="purchase_order_id")
    po_number = django_filters.CharFilter(
        field_name="purchase_order__po_number", lookup_expr="iexact"
    )
    invoice_number = django_filters.CharFilter(lookup_expr="iexact")
    invoice_date_from = django_filters.DateFilter(field_name="invoice_date", lookup_expr="gte")
    invoice_date_to = django_filters.DateFilter(field_name="invoice_date", lookup_expr="lte")
    due_date_from = django_filters.DateFilter(field_name="due_date", lookup_expr="gte")
    due_date_to = django_filters.DateFilter(field_name="due_date", lookup_expr="lte")
    min_total = django_filters.NumberFilter(field_name="total_amount", lookup_expr="gte")
    max_total = django_filters.NumberFilter(field_name="total_amount", lookup_expr="lte")

    class Meta:
        model = Invoice
        fields = ["reconciliation_status", "source", "vendor", "purchase_order"]
