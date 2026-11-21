from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from shared.enums import VendorStatus

from .filters import VendorFilter
from .models import Vendor
from .serializers import VendorSerializer


class VendorViewSet(viewsets.ModelViewSet):
    """Vendor master data.

    Vendors referenced by purchase orders or invoices cannot be deleted
    (409); deactivate them instead.
    """

    queryset = Vendor.objects.all()
    serializer_class = VendorSerializer
    filterset_class = VendorFilter
    search_fields = ["vendor_code", "name", "email", "tax_id"]
    ordering_fields = ["id", "vendor_code", "name", "status", "created_at"]
    ordering = ["id"]

    def _set_status(self, new_status: VendorStatus) -> Response:
        vendor = self.get_object()
        if vendor.status != new_status.value:
            vendor.status = new_status.value
            vendor.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(vendor).data)

    @extend_schema(request=None, responses=VendorSerializer)
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        return self._set_status(VendorStatus.ACTIVE)

    @extend_schema(request=None, responses=VendorSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self._set_status(VendorStatus.INACTIVE)
