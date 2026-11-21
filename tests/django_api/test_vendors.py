import pytest
from django.db import IntegrityError, transaction

from apps.vendors.models import Vendor

pytestmark = pytest.mark.django_db
URL = "/api/v1/vendors/"


def test_create_vendor_normalises_code_and_defaults_to_active(api):
    response = api.post(URL, {"vendor_code": " vend-001 ", "name": " Acme Supplies ",
                              "email": "ap@acme.test", "tax_id": "gst123"})

    assert response.status_code == 201
    body = response.json()
    assert body["vendor_code"] == "VEND-001"
    assert body["name"] == "Acme Supplies"
    assert body["tax_id"] == "GST123"
    assert body["status"] == "ACTIVE"
    assert Vendor.objects.get().vendor_code == "VEND-001"


def test_vendor_code_must_be_unique_case_insensitively(api, vendor):
    response = api.post(URL, {"vendor_code": "v001", "name": "Another"})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert "already exists" in error["details"]["vendor_code"][0]
    assert Vendor.objects.count() == 1


def test_tax_id_is_unique_but_optional(api, make_vendor):
    make_vendor("V001", tax_id="GST1")
    assert api.post(URL, {"vendor_code": "V002", "name": "B", "tax_id": "gst1"}).status_code == 400
    # Blank tax ids are stored as NULL, so any number of vendors may omit it.
    assert api.post(URL, {"vendor_code": "V003", "name": "C", "tax_id": ""}).status_code == 201
    assert api.post(URL, {"vendor_code": "V004", "name": "D"}).status_code == 201
    assert Vendor.objects.filter(tax_id__isnull=True).count() == 2


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"name": "No code"}, "vendor_code"),
        ({"vendor_code": "V9"}, "name"),
        ({"vendor_code": "   ", "name": "Blank code"}, "vendor_code"),
        ({"vendor_code": "V9", "name": "X", "email": "not-an-email"}, "email"),
        ({"vendor_code": "V9", "name": "X", "status": "DELETED"}, "status"),
    ],
)
def test_create_vendor_validation(api, db, payload, field):
    response = api.post(URL, payload)
    assert response.status_code == 400
    assert field in response.json()["error"]["details"]


def test_retrieve_update_and_partial_update(api, vendor):
    detail = f"{URL}{vendor.id}/"
    assert api.get(detail).json()["vendor_code"] == "V001"

    response = api.patch(detail, {"name": "Acme Renamed", "phone": "555-0100"})
    assert response.status_code == 200
    assert response.json()["name"] == "Acme Renamed"

    # Re-submitting its own code on a full update is not a uniqueness clash.
    response = api.put(detail, {"vendor_code": "V001", "name": "Acme Again"})
    assert response.status_code == 200
    vendor.refresh_from_db()
    assert (vendor.name, vendor.phone) == ("Acme Again", "555-0100")


def test_activate_and_deactivate(api, vendor):
    response = api.post(f"{URL}{vendor.id}/deactivate/")
    assert response.status_code == 200
    assert response.json()["status"] == "INACTIVE"

    assert api.post(f"{URL}{vendor.id}/activate/").json()["status"] == "ACTIVE"
    vendor.refresh_from_db()
    assert vendor.is_active


def test_list_is_paginated(api, make_vendor):
    for i in range(1, 6):
        make_vendor(f"V{i:03d}")

    body = api.get(URL, {"page_size": 2, "page": 2}).json()

    assert (body["count"], body["page"], body["page_size"], body["total_pages"]) == (5, 2, 2, 3)
    assert [v["vendor_code"] for v in body["results"]] == ["V003", "V004"]
    assert body["next"] and body["previous"]


def test_list_filter_search_and_ordering(api, make_vendor):
    make_vendor("V001", name="Acme Supplies")
    make_vendor("V002", name="Globex Traders", status="INACTIVE")
    make_vendor("V003", name="Acme Logistics", email="ops@logi.test")

    def codes(**params):
        return [v["vendor_code"] for v in api.get(URL, params).json()["results"]]

    assert codes(status="INACTIVE") == ["V002"]
    assert codes(name="acme") == ["V001", "V003"]
    assert codes(vendor_code="v003") == ["V003"]
    assert codes(search="logi") == ["V003"]
    assert codes(ordering="-vendor_code") == ["V003", "V002", "V001"]
    assert api.get(URL, {"status": "BOGUS"}).status_code == 400


def test_delete_unreferenced_vendor(api, vendor):
    assert api.delete(f"{URL}{vendor.id}/").status_code == 204
    assert not Vendor.objects.exists()


def test_vendor_with_purchase_orders_cannot_be_deleted(api, vendor, make_po):
    make_po(vendor)

    response = api.delete(f"{URL}{vendor.id}/")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "protected_resource"
    assert Vendor.objects.filter(pk=vendor.pk).exists()


def test_unknown_vendor_uses_error_envelope(api, db):
    response = api.get(f"{URL}999/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_database_enforces_unique_code_and_valid_status(vendor):
    with pytest.raises(IntegrityError), transaction.atomic():
        Vendor.objects.create(vendor_code="V001", name="Bypasses the serializer")
    with pytest.raises(IntegrityError), transaction.atomic():
        Vendor.objects.create(vendor_code="V002", name="Bad status", status="WHATEVER")
