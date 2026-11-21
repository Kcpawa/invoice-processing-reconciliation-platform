"""Upload history, vendor summary, PO balance report and statistics."""
from datetime import timedelta

import pytest

from tests.helpers import TODAY, invoice_row, make_csv


@pytest.fixture
def dataset(upload, make_vendor, make_po):
    """Two vendors, three POs and one processed upload:
    V001: INV-1 matched (1180), INV-2 mismatched (1180, PO-SMALL has 500)
    V002: INV-3 matched (1180)
    plus one duplicate row and one failed row.
    """
    v1 = make_vendor("V001", name="Acme Supplies")
    v2 = make_vendor("V002", name="Globex Traders")
    make_vendor("V003", name="Idle Corp", status="INACTIVE")
    make_po(v1, "PO-001", amount="10000.00")
    make_po(v1, "PO-SMALL", amount="500.00")
    make_po(v2, "PO-002", amount="1180.00")
    response = upload(
        make_csv([
            invoice_row("INV-1"),
            invoice_row("INV-2", po_number="PO-SMALL"),
            invoice_row("INV-3", vendor_code="V002", po_number="PO-002"),
            invoice_row("INV-1"),
            invoice_row("INV-4", vendor_code="GHOST"),
        ]),
        filename="november.csv",
    )
    assert response.status_code == 201
    return response.json()


# ------------------------------------------------------------------ history
def test_upload_history_lists_batches_newest_first(client, upload, dataset):
    upload(make_csv([invoice_row("INV-9")]), filename="second.csv")

    body = client.get("/api/v1/uploads").json()

    assert body["count"] == 2
    assert [b["file_name"] for b in body["results"]] == ["second.csv", "november.csv"]
    first = body["results"][1]
    assert (first["total_records"], first["success_count"], first["failure_count"],
            first["duplicate_count"]) == (5, 3, 1, 1)


def test_upload_history_filters_and_pagination(client, upload, dataset):
    upload(make_csv([invoice_row("INV-9")]), filename="second.csv")

    assert client.get("/api/v1/uploads", params={"file_name": "NOVEM"}).json()["count"] == 1
    assert client.get("/api/v1/uploads", params={"status": "FAILED"}).json()["count"] == 0
    assert client.get("/api/v1/uploads", params={"status": "COMPLETED"}).json()["count"] == 2
    tomorrow = (TODAY + timedelta(days=2)).isoformat()
    assert client.get("/api/v1/uploads", params={"created_from": tomorrow}).json()["count"] == 0

    page2 = client.get("/api/v1/uploads", params={"page": 2, "page_size": 1}).json()
    assert (page2["count"], page2["page"], page2["page_size"], page2["total_pages"]) == (2, 2, 1, 2)
    assert [b["file_name"] for b in page2["results"]] == ["november.csv"]


def test_invalid_pagination_is_rejected(client):
    assert client.get("/api/v1/uploads", params={"page": 0}).status_code == 422
    assert client.get("/api/v1/uploads", params={"page_size": 1000}).status_code == 422
    assert client.get("/api/v1/uploads", params={"status": "BOGUS"}).status_code == 422


def test_upload_detail_and_404(client, dataset):
    batch_id = dataset["summary"]["id"]
    assert client.get(f"/api/v1/uploads/{batch_id}").json() == dataset["summary"]

    missing = client.get("/api/v1/uploads/9999")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert client.get("/api/v1/uploads/9999/records").status_code == 404


def test_upload_records_can_be_filtered_by_classification(client, dataset):
    url = f"/api/v1/uploads/{dataset['summary']['id']}/records"

    everything = client.get(url).json()
    assert everything["count"] == 5
    assert [r["row_number"] for r in everything["results"]] == [2, 3, 4, 5, 6]

    duplicates = client.get(url, params={"classification": "DUPLICATE"}).json()
    assert [r["row_number"] for r in duplicates["results"]] == [5]

    paged = client.get(url, params={"page_size": 2, "page": 3}).json()
    assert [r["row_number"] for r in paged["results"]] == [6]
    assert paged["total_pages"] == 3


# ----------------------------------------------------------- vendor summary
def test_vendor_summary(client, dataset):
    body = client.get("/api/v1/reports/vendor-summary").json()

    assert body["count"] == 3
    by_code = {v["vendor_code"]: v for v in body["results"]}
    assert list(by_code) == ["V001", "V002", "V003"]

    acme = by_code["V001"]
    assert acme["vendor_name"] == "Acme Supplies"
    assert acme["invoice_count"] == 2
    assert acme["total_invoiced_amount"] == "2360.00"
    assert (acme["matched_count"], acme["matched_amount"]) == (1, "1180.00")
    assert (acme["mismatched_count"], acme["mismatched_amount"]) == (1, "1180.00")
    assert (acme["pending_count"], acme["failed_count"]) == (0, 0)
    assert acme["last_invoice_date"] is not None

    idle = by_code["V003"]  # vendors without invoices are still listed
    assert (idle["invoice_count"], idle["total_invoiced_amount"]) == (0, "0.00")
    assert idle["last_invoice_date"] is None


def test_vendor_summary_filters(client, dataset):
    url = "/api/v1/reports/vendor-summary"

    assert [v["vendor_code"] for v in
            client.get(url, params={"vendor_code": "v002"}).json()["results"]] == ["V002"]
    assert [v["vendor_code"] for v in
            client.get(url, params={"vendor_status": "INACTIVE"}).json()["results"]] == ["V003"]
    assert [v["vendor_code"] for v in
            client.get(url, params={"search": "globex"}).json()["results"]] == ["V002"]

    # A period with no invoices keeps the vendors but zeroes the figures.
    future = client.get(url, params={"invoice_date_from": TODAY.isoformat()}).json()
    assert future["count"] == 3
    assert {v["invoice_count"] for v in future["results"]} == {0}

    page = client.get(url, params={"page_size": 2, "page": 2}).json()
    assert [v["vendor_code"] for v in page["results"]] == ["V003"]


# --------------------------------------------------------------- PO balance
def test_po_balance_report(client, dataset):
    body = client.get("/api/v1/reports/po-balance").json()

    assert body["count"] == 3
    by_number = {p["po_number"]: p for p in body["results"]}

    main = by_number["PO-001"]
    assert (main["total_amount"], main["invoiced_amount"], main["remaining_balance"]) == (
        "10000.00", "1180.00", "8820.00",
    )
    assert main["utilization_percent"] == "11.80"
    assert (main["matched_invoice_count"], main["unmatched_invoice_count"]) == (1, 0)
    assert (main["vendor_code"], main["status"]) == ("V001", "OPEN")

    small = by_number["PO-SMALL"]  # the mismatched invoice consumed nothing
    assert (small["remaining_balance"], small["unmatched_invoice_count"]) == ("500.00", 1)

    used_up = by_number["PO-002"]
    assert (used_up["remaining_balance"], used_up["status"]) == ("0.00", "CLOSED")
    assert used_up["utilization_percent"] == "100.00"

    assert body["totals"] == {
        "total_amount": "11680.00", "invoiced_amount": "2360.00",
        "remaining_balance": "9320.00",
    }


def test_po_balance_filters(client, dataset):
    url = "/api/v1/reports/po-balance"

    def numbers(**params):
        return [p["po_number"] for p in client.get(url, params=params).json()["results"]]

    assert numbers(vendor_code="V001") == ["PO-001", "PO-SMALL"]
    assert numbers(status="CLOSED") == ["PO-002"]
    assert numbers(has_balance="true") == ["PO-001", "PO-SMALL"]
    assert numbers(has_balance="false") == ["PO-002"]
    assert numbers(po_number="po-small") == ["PO-SMALL"]
    assert numbers(order_date_from=TODAY.isoformat()) == []

    filtered = client.get(url, params={"vendor_code": "V001", "page_size": 1}).json()
    assert (filtered["count"], filtered["total_pages"]) == (2, 2)
    # Totals cover all filtered POs, not just the page.
    assert filtered["totals"]["total_amount"] == "10500.00"


# --------------------------------------------------------------- statistics
def test_processing_statistics(client, dataset, make_invoice, sa_session):
    body = client.get("/api/v1/reports/processing-stats").json()

    uploads = body["uploads"]
    assert uploads["total_batches"] == 1
    assert uploads["batches_by_status"] == {"PROCESSING": 0, "COMPLETED": 1, "FAILED": 0}
    assert (uploads["total_records"], uploads["success_count"], uploads["failure_count"],
            uploads["duplicate_count"]) == (5, 3, 1, 1)
    assert (uploads["matched_count"], uploads["mismatched_count"]) == (2, 1)
    assert uploads["success_rate_percent"] == "60.00"

    invoices = body["invoices"]
    assert invoices["total_invoices"] == 3
    assert invoices["total_amount"] == "3540.00"
    assert invoices["by_status"]["MATCHED"] == {"count": 2, "total_amount": "2360.00"}
    assert invoices["by_status"]["MISMATCHED"] == {"count": 1, "total_amount": "1180.00"}
    assert invoices["by_status"]["PENDING"] == {"count": 0, "total_amount": "0.00"}
    assert invoices["by_source"] == {"API": 0, "BULK_UPLOAD": 3}


def test_processing_statistics_on_empty_database_and_date_filter(client, dataset):
    future = (TODAY + timedelta(days=2)).isoformat()
    body = client.get("/api/v1/reports/processing-stats", params={"date_from": future}).json()

    assert body["date_from"] == future
    assert body["uploads"]["total_batches"] == 0
    assert body["uploads"]["success_rate_percent"] == "0.00"
    assert body["invoices"]["total_invoices"] == 0


def test_health_and_openapi(client):
    assert client.get("/health").json() == {
        "status": "ok", "service": "fastapi", "database": "ok",
    }
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/uploads/invoices" in paths
    assert "/api/v1/reports/po-balance" in paths
    assert "/api/v1/reconciliation/run" in paths
