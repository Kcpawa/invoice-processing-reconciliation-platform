from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.invoices.models import Invoice
from apps.purchase_orders.models import PurchaseOrder
from apps.vendors.models import Vendor
from tests.helpers import DUE_DATE, INVOICE_DATE, PO_DATE


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def vendor(db):
    return Vendor.objects.create(vendor_code="V001", name="Acme Supplies")


@pytest.fixture
def make_vendor(db):
    def _make(vendor_code, name=None, **kwargs):
        return Vendor.objects.create(
            vendor_code=vendor_code, name=name or f"Vendor {vendor_code}", **kwargs
        )

    return _make


@pytest.fixture
def make_po(db):
    def _make(vendor, po_number="PO-001", amount="10000.00", tax="0.00", **kwargs):
        total = Decimal(amount) + Decimal(tax)
        defaults = {
            "order_date": PO_DATE, "amount": Decimal(amount), "tax_amount": Decimal(tax),
            "total_amount": total, "remaining_balance": total,
        }
        defaults.update(kwargs)
        return PurchaseOrder.objects.create(po_number=po_number, vendor=vendor, **defaults)

    return _make


@pytest.fixture
def make_invoice(db):
    def _make(vendor, po, invoice_number="INV-001", amount="1000.00", tax="0.00", **kwargs):
        defaults = {
            "invoice_date": INVOICE_DATE, "due_date": DUE_DATE, "amount": Decimal(amount),
            "tax_amount": Decimal(tax), "total_amount": Decimal(amount) + Decimal(tax),
        }
        defaults.update(kwargs)
        return Invoice.objects.create(
            invoice_number=invoice_number, vendor=vendor, purchase_order=po, **defaults
        )

    return _make


@pytest.fixture
def matched_invoice(vendor, make_po, make_invoice):
    """An invoice of 1,000 already reconciled against a 5,000 PO (4,000 left)."""
    po = make_po(vendor, "PO-001", amount="5000.00", remaining_balance=Decimal("4000.00"))
    invoice = make_invoice(vendor, po, "INV-001", amount="1000.00",
                           reconciliation_status="MATCHED")
    return invoice
