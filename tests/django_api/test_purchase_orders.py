from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.purchase_orders.models import PurchaseOrder
from tests.helpers import PO_DATE, TODAY

pytestmark = pytest.mark.django_db
URL = "/api/v1/purchase-orders/"


def payload(for_vendor, **overrides):
    data = {"po_number": "po-001", "vendor": for_vendor.id, "order_date": PO_DATE.isoformat(),
            "amount": "1000.00", "tax_amount": "180.00", "description": "Laptops"}
    data.update(overrides)
    return data


def test_create_po_computes_total_and_balance(api, vendor):
    response = api.post(URL, payload(vendor))

    assert response.status_code == 201
    body = response.json()
    assert body["po_number"] == "PO-001"
    assert (body["vendor_code"], body["vendor_name"]) == ("V001", "Acme Supplies")
    assert body["total_amount"] == "1180.00"
    assert body["remaining_balance"] == "1180.00"
    assert body["invoiced_amount"] == "0.00"
    assert body["status"] == "OPEN"


def test_system_managed_fields_cannot_be_set_by_the_client(api, vendor):
    response = api.post(URL, payload(vendor, total_amount="1.00", remaining_balance="999999.00"))
    body = response.json()
    assert (body["total_amount"], body["remaining_balance"]) == ("1180.00", "1180.00")


def test_tax_defaults_to_zero(api, vendor):
    data = payload(vendor)
    del data["tax_amount"]
    body = api.post(URL, data).json()
    assert (body["tax_amount"], body["total_amount"]) == ("0.00", "1000.00")


def test_duplicate_po_number_is_rejected(api, vendor, make_po):
    make_po(vendor, "PO-001")

    response = api.post(URL, payload(vendor, po_number=" po-001 "))

    assert response.status_code == 400
    assert "already exists" in response.json()["error"]["details"]["po_number"][0]
    assert PurchaseOrder.objects.count() == 1


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"amount": "0"}, "amount"),
        ({"amount": "-10"}, "amount"),
        ({"tax_amount": "-1"}, "tax_amount"),
        ({"vendor": 9999}, "vendor"),
        ({"order_date": "not-a-date"}, "order_date"),
        ({"po_number": ""}, "po_number"),
        ({"status": "ARCHIVED"}, "status"),
    ],
)
def test_create_po_validation(api, vendor, overrides, field):
    response = api.post(URL, payload(vendor, **overrides))
    assert response.status_code == 400
    assert field in response.json()["error"]["details"]


def test_po_cannot_be_raised_for_inactive_vendor(api, make_vendor):
    inactive = make_vendor("V009", status="INACTIVE")
    response = api.post(URL, payload(inactive))
    assert response.status_code == 400
    assert "active vendors" in response.json()["error"]["details"]["vendor"][0]


def test_amending_amount_preserves_what_was_already_invoiced(api, vendor, make_po):
    po = make_po(vendor, amount="1000.00", remaining_balance=Decimal("400.00"))  # 600 invoiced

    response = api.patch(f"{URL}{po.id}/", {"amount": "1500.00", "tax_amount": "100.00"})

    assert response.status_code == 200
    body = response.json()
    assert (body["total_amount"], body["invoiced_amount"], body["remaining_balance"]) == (
        "1600.00", "600.00", "1000.00",
    )


def test_total_cannot_drop_below_invoiced_amount(api, vendor, make_po):
    po = make_po(vendor, amount="1000.00", remaining_balance=Decimal("400.00"))

    response = api.patch(f"{URL}{po.id}/", {"amount": "599.99"})

    assert response.status_code == 400
    assert "already invoiced" in response.json()["error"]["details"]["amount"][0]
    po.refresh_from_db()
    assert po.total_amount == Decimal("1000.00")


def test_reducing_total_to_invoiced_amount_closes_the_po(api, vendor, make_po):
    po = make_po(vendor, amount="1000.00", remaining_balance=Decimal("400.00"))

    body = api.patch(f"{URL}{po.id}/", {"amount": "600.00"}).json()

    assert (body["remaining_balance"], body["status"]) == ("0.00", "CLOSED")


def test_cancel_po_and_update_without_touching_amounts(api, vendor, make_po):
    po = make_po(vendor, amount="1000.00", remaining_balance=Decimal("400.00"))

    body = api.patch(f"{URL}{po.id}/", {"status": "CANCELLED", "description": "x"}).json()

    assert body["status"] == "CANCELLED"
    assert body["remaining_balance"] == "400.00"


def test_vendor_cannot_change_once_invoiced(api, vendor, make_vendor, make_po, make_invoice):
    other = make_vendor("V002")
    free_po = make_po(vendor, "PO-FREE")
    used_po = make_po(vendor, "PO-USED")
    make_invoice(vendor, used_po)

    assert api.patch(f"{URL}{free_po.id}/", {"vendor": other.id}).status_code == 200
    response = api.patch(f"{URL}{used_po.id}/", {"vendor": other.id})
    assert response.status_code == 400
    assert "vendor" in response.json()["error"]["details"]


def test_list_filters(api, vendor, make_vendor, make_po):
    other = make_vendor("V002")
    make_po(vendor, "PO-001", amount="1000.00")
    make_po(vendor, "PO-002", amount="5000.00", status="CLOSED",
            remaining_balance=Decimal("0.00"))
    make_po(other, "PO-003", amount="9000.00", order_date=TODAY)

    def numbers(**params):
        return [p["po_number"] for p in api.get(URL, params).json()["results"]]

    assert numbers() == ["PO-001", "PO-002", "PO-003"]
    assert numbers(vendor=vendor.id) == ["PO-001", "PO-002"]
    assert numbers(vendor_code="v002") == ["PO-003"]
    assert numbers(status="CLOSED") == ["PO-002"]
    assert numbers(order_date_from=TODAY.isoformat()) == ["PO-003"]
    assert numbers(min_remaining_balance="5000") == ["PO-003"]
    assert numbers(max_remaining_balance="0") == ["PO-002"]
    assert numbers(search="PO-00", ordering="-total_amount", page_size=2) == ["PO-003", "PO-002"]


def test_delete_po(api, vendor, make_po, make_invoice):
    free_po = make_po(vendor, "PO-FREE")
    used_po = make_po(vendor, "PO-USED")
    make_invoice(vendor, used_po)

    assert api.delete(f"{URL}{free_po.id}/").status_code == 204
    response = api.delete(f"{URL}{used_po.id}/")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "protected_resource"
    assert PurchaseOrder.objects.filter(pk=used_po.pk).exists()


@pytest.mark.parametrize(
    "bad_values",
    [
        {"remaining_balance": Decimal("-1.00")},            # overdrawn
        {"remaining_balance": Decimal("2000.00")},          # more than the total
        {"total_amount": Decimal("5000.00"), "remaining_balance": Decimal("0")},  # != amount+tax
        {"status": "WHATEVER"},
    ],
)
def test_database_check_constraints(vendor, make_po, bad_values):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_po(vendor, amount="1000.00", **bad_values)
