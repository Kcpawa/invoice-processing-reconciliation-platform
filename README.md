# Invoice Processing & Reconciliation Platform

A backend that automates vendor invoice intake: it stores vendors, purchase
orders (POs) and invoices, bulk-imports invoices from CSV/Excel, and reconciles
every invoice against its PO.

| Requirement | Where |
|---|---|
| Python, PostgreSQL | Python 3.11+, PostgreSQL 14+ |
| Django & Django REST Framework + Django ORM | `config/`, `apps/` - CRUD for vendors, POs, invoices; owns the schema and migrations |
| FastAPI + SQLAlchemy + Pydantic | `fastapi_app/` - bulk upload, reconciliation, history, reports |
| Pytest | `tests/` - 228 tests |

## Contents

1. [Architecture](#architecture)
2. [Quick start](#quick-start)
3. [Try it with the sample file](#try-it-with-the-sample-file)
4. [API overview](#api-overview)
5. [Business rules](#business-rules)
6. [Idempotency and transactions](#idempotency-and-transactions)
7. [Database design](#database-design)
8. [Tests](#tests)
9. [Project structure](#project-structure)
10. [Assumptions and limitations](#assumptions-and-limitations)

## Architecture

Two services, one PostgreSQL database.

```
                 ┌──────────────────────────────┐   ┌──────────────────────────────────┐
  CRUD clients ─▶│ Django REST Framework  :8000 │   │ FastAPI                    :8001 │◀─ uploads,
                 │ vendors / POs / invoices     │   │ bulk upload · reconciliation     │   reports
                 │ Django ORM · migrations      │   │ history · reports                │
                 └──────────────┬───────────────┘   │ SQLAlchemy · Pydantic            │
                                │                   └────────────────┬─────────────────┘
                                │      shared/  (enums + rule engine, pure Python)
                                ▼                                    ▼
                        ┌──────────────────────────────────────────────────┐
                        │                   PostgreSQL                     │
                        │ vendors · purchase_orders · invoices             │
                        │ upload_batches · upload_records                  │
                        └──────────────────────────────────────────────────┘
```

* **Django owns the schema.** Every table, constraint and index is defined in
  Django models and created by Django migrations.
* **FastAPI maps the same tables with SQLAlchemy.** A test
  (`tests/test_schema_parity.py`) fails if the two sets of models drift apart.
* **`shared/rules.py` holds the reconciliation rules** as pure functions with
  no database access, so both services classify invoices identically and the
  rules are unit-tested in isolation.

## Quick start

Prerequisites: Python 3.11+ and a PostgreSQL server (or Docker).

### Option A - local Python, PostgreSQL in Docker

```bash
# 1. Database
docker compose up -d db              # or use any PostgreSQL and: CREATE DATABASE invoice_platform;

# 2. Environment
python -m venv .venv
.venv\Scripts\activate               # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env               # Windows   (Linux/macOS: cp .env.example .env)
#    edit DB_USER / DB_PASSWORD / DB_HOST in .env if yours differ

# 3. Schema + demo data
python manage.py migrate
python manage.py seed_demo_data      # 4 vendors and 5 POs used by the sample files

# 4. Run both services (two terminals)
python manage.py runserver 8000
uvicorn fastapi_app.main:app --port 8001 --reload
```

### Option B - everything in Docker

```bash
docker compose up --build
```

This starts PostgreSQL, runs the migrations, seeds the demo data and starts
both services.

### URLs

| | Django (master data) | FastAPI (processing) |
|---|---|---|
| Swagger UI | http://localhost:8000/api/docs/ | http://localhost:8001/docs |
| ReDoc | http://localhost:8000/api/redoc/ | http://localhost:8001/redoc |
| OpenAPI spec | http://localhost:8000/api/schema/ | http://localhost:8001/openapi.json |
| Health | http://localhost:8000/health/ | http://localhost:8001/health |

Static copies of both specs are in `docs/`, together with the Postman
collection (`docs/postman_collection.json` - import it into Postman; the
`django_url` and `fastapi_url` variables default to the ports above).

## Try it with the sample file

`samples/sample_invoices.csv` and `samples/sample_invoices.xlsx` contain the
same 14 rows, written against the demo data from `seed_demo_data`.

```bash
curl -F "file=@samples/sample_invoices.csv" http://localhost:8001/api/v1/uploads/invoices
```

(In Windows PowerShell use `curl.exe` instead of `curl`, or upload the file
from Swagger UI at http://localhost:8001/docs.)

Expected summary: **14 records - 6 imported (4 matched, 2 mismatched), 7 failed, 1 duplicate.**

| Row | Invoice | Result | Why |
|---|---|---|---|
| 2 | INV-1001 | MATCHED | |
| 3 | INV-1002 | MATCHED | |
| 4 | INV-2001 | MATCHED | |
| 5 | INV-1003 | MISMATCHED | 3,540 exceeds PO-1002's balance of 2,360 |
| 6 | INV-2002 | MISMATCHED | PO-1001 belongs to V001, not V002 |
| 7 | INV-1001 | DUPLICATE | repeats row 2 |
| 8 | INV-9001 | FAILED | vendor V999 does not exist |
| 9 | INV-1004 | FAILED | PO-9999 does not exist |
| 10 | INV-3001 | FAILED | vendor V003 is inactive |
| 11 | INV-1005 | FAILED | due date before invoice date |
| 12 | INV-1006 | FAILED | amount is not a number |
| 13 | INV-1007 | FAILED | total does not equal amount + tax |
| 14 | INV-4001 | FAILED | PO-4001 is cancelled |
| 15 | INV-2003 | MATCHED | tax and total left blank (defaulted) |

Run the same command again: the response is HTTP 200 with
`"idempotent_replay": true` and nothing is processed twice.

Then look at the reports:

```bash
curl http://localhost:8001/api/v1/reports/po-balance
curl http://localhost:8001/api/v1/reports/vendor-summary
curl http://localhost:8001/api/v1/reports/processing-stats
```

### Upload file format

A header row is required. Column order and letter case do not matter.

| Column | Required | Notes |
|---|---|---|
| `invoice_number` | yes | max 50 characters |
| `vendor_code` | yes | must match an active vendor |
| `po_number` | yes | must match a PO |
| `invoice_date` | yes | `YYYY-MM-DD`, `DD/MM/YYYY`, `DD-MM-YYYY`, `DD-Mon-YYYY`, or a real Excel date |
| `due_date` | yes | same formats |
| `amount` | yes | pre-tax amount, greater than 0 |
| `tax_amount` | no | defaults to 0 |
| `total_amount` | no | defaults to amount + tax; if given it must equal amount + tax |

Common header variants are accepted (`Invoice No`, `Vendor`, `PO Reference`,
`Tax`, `Total`, ...). Supported types are `.csv` and `.xlsx` (first sheet).
Limits: 5 MB and 5,000 rows per file, both configurable in `.env`.

## API overview

### Django REST Framework - `http://localhost:8000/api/v1/`

| Resource | Endpoints |
|---|---|
| Vendors | `GET/POST /vendors/`, `GET/PUT/PATCH/DELETE /vendors/{id}/`, `POST /vendors/{id}/activate/`, `POST /vendors/{id}/deactivate/` |
| Purchase orders | `GET/POST /purchase-orders/`, `GET/PUT/PATCH/DELETE /purchase-orders/{id}/` |
| Invoices | `GET/POST /invoices/`, `GET/PUT/PATCH/DELETE /invoices/{id}/` |

Every list endpoint supports `?page=`, `?page_size=` (max 100), `?search=`,
`?ordering=` and field filters, for example:

```
/vendors/?status=ACTIVE&search=acme
/purchase-orders/?vendor_code=V001&status=OPEN&min_remaining_balance=1000
/invoices/?reconciliation_status=MISMATCHED&invoice_date_from=2026-01-01&ordering=-total_amount
```

### FastAPI - `http://localhost:8001/api/v1/`

| Area | Endpoint | Purpose |
|---|---|---|
| Bulk upload | `POST /uploads/invoices` | Upload a CSV/XLSX file (multipart field `file`) |
| History | `GET /uploads` | Upload history; filter by `status`, `file_name`, `created_from/to` |
| | `GET /uploads/{id}` | Processing summary of one upload |
| | `GET /uploads/{id}/records` | Row-level results; filter by `classification` |
| Reconciliation | `POST /reconciliation/run` | Classify all PENDING and MISMATCHED invoices |
| | `POST /reconciliation/invoices/{id}` | Classify one invoice |
| Reports | `GET /reports/vendor-summary` | Vendor-wise invoice counts and amounts by status |
| | `GET /reports/po-balance` | PO-wise total, invoiced and remaining amounts, with grand totals |
| | `GET /reports/processing-stats` | Upload and invoice statistics |

List endpoints take `?page=` and `?page_size=` (max 200).

### Responses

Paginated lists from both services use the same envelope:

```json
{ "count": 42, "page": 1, "page_size": 20, "total_pages": 3, "results": [ ... ] }
```

Errors from both services use the same envelope:

```json
{ "error": { "code": "validation_error", "message": "Invalid input.", "details": { "po_number": ["..."] } } }
```

| Status | Meaning |
|---|---|
| 400 | Invalid input, or an unreadable/unsupported upload file |
| 404 | Record not found |
| 409 | Conflict: deleting a referenced record, idempotency-key misuse, upload already in progress |
| 413 | Upload larger than the size limit |
| 422 | Invalid query/body parameters (FastAPI) |

## Business rules

### Classification

Each uploaded row, and each stored invoice that is reconciled, gets exactly one
classification. The first rule that applies wins.

| # | Classification | Applies when | Stored as an invoice? |
|---|---|---|---|
| 1 | **DUPLICATE** | The same vendor + invoice number already exists, or appeared earlier in the same file | No |
| 2 | **FAILED** | Required field missing or unparseable; vendor unknown or inactive; PO unknown or cancelled; invoice date in the future; due date before invoice date; amount ≤ 0; total ≠ amount + tax | No |
| 3 | **MISMATCHED** | The invoice is valid but disagrees with its PO: the PO belongs to another vendor, the PO is closed, the invoice is dated before the PO, or the total exceeds the PO's remaining balance | Yes, for review; the PO balance is **not** reduced |
| 4 | **MATCHED** | Everything agrees | Yes; the PO balance is reduced by the invoice total |

Every non-matched result carries machine-readable reasons, for example
`{"code": "EXCEEDS_PO_BALANCE", "message": "Invoice total 3540.00 exceeds the remaining PO balance 2360.00."}`.

The upload summary reports `success_count` (rows imported = matched +
mismatched), `failure_count` (failed) and `duplicate_count`; these three always
add up to `total_records`. `matched_count` and `mismatched_count` give the
breakdown, and the response lists `valid_records` and `invalid_records`
separately.

### Invoice lifecycle

```
            created through the Django API
                        │
                        ▼
                    PENDING ──reconcile──▶ MATCHED      (PO balance reduced)
                        ▲                  MISMATCHED   (retried on every run)
                        │                  FAILED       (retried with include_failed=true)
       edited through the Django API
```

* Invoices created through the Django API start as `PENDING`; call
  `POST /reconciliation/run` on the FastAPI service to classify them.
  Invoices imported by bulk upload are classified during the upload.
* A `MISMATCHED` invoice is re-checked on every reconciliation run, so it
  becomes `MATCHED` once the underlying problem is fixed (for example after the
  PO amount is increased).
* Editing a financial field of an invoice resets it to `PENDING`. If it was
  `MATCHED`, its total is returned to the PO balance. Deleting a `MATCHED`
  invoice also returns its total.

### Purchase order balance

* `total_amount = amount + tax_amount` and `remaining_balance` are maintained
  by the system and cannot be set by clients.
* The balance only changes when an invoice becomes `MATCHED` (reduced) or a
  matched invoice is edited or deleted (restored).
* A PO whose balance reaches zero is set to `CLOSED`; it reopens if balance is
  restored.
* Amending a PO's amount keeps what was already invoiced:
  `remaining = new total - invoiced`. A total below the invoiced amount is
  rejected.

### Master data rules

* `vendor_code`, `po_number` and `invoice_number` are trimmed and stored
  upper-case, so `inv-001` and `INV-001` are the same invoice.
* `vendor_code` and `po_number` are unique. `invoice_number` is unique per
  vendor. A vendor's `tax_id` is unique when present.
* A PO can only be raised for an active vendor.
* Vendors and POs that are referenced by other records cannot be deleted (409);
  deactivate the vendor or cancel the PO instead.

## Idempotency and transactions

### Bulk upload idempotency

Each upload has an idempotency key, stored in a unique column:

* the `Idempotency-Key` request header if the client sends one, otherwise
* `sha256:<hash of the file content>`.

| Situation | Result |
|---|---|
| Same key and same file again | HTTP 200, the stored result, `idempotent_replay: true`. Nothing is processed again. |
| Same key with a different file | HTTP 409 `idempotency_key_reused` |
| Same upload still being processed | HTTP 409 `upload_in_progress` |
| Previous attempt failed or was abandoned | The upload is processed again under the same batch |
| `?force=true` (no header) | Identical content is processed as a new batch; rows already imported are reported as duplicates |

There is a second line of defence below the batch level: the unique constraint
on (vendor, invoice number) means no invoice can be imported twice even when
different files or keys are used.

### Transactions and concurrency

* **A file is applied completely or not at all.** All rows and the batch
  counters are written in one transaction. If processing crashes, everything is
  rolled back, the batch is marked `FAILED`, and the same request can be
  retried.
* **One bad row never rejects the file.** Row problems are recorded as `FAILED`
  or `DUPLICATE` records. Each insert runs inside a savepoint, so even an
  unexpected constraint violation only affects that row.
* **PO balances cannot be overdrawn.** Every code path that changes a balance
  first locks the PO row (`SELECT ... FOR UPDATE`). POs are locked in id order
  to avoid deadlocks. A database check constraint
  (`0 <= remaining_balance <= total_amount`) backs this up.
* The concurrency behaviour is covered by tests that run parallel
  reconciliations and parallel uploads against PostgreSQL
  (`tests/fastapi_api/test_concurrency.py`).

## Database design

`docs/schema.sql` contains the full DDL generated from the migrations.

| Table | Key columns | Constraints and indexes |
|---|---|---|
| `vendors` | `vendor_code`, `name`, `tax_id`, `status` | unique `vendor_code`; unique `tax_id`; status check; indexes on `status`, `name` |
| `purchase_orders` | `po_number`, `vendor_id`, `amount`, `tax_amount`, `total_amount`, `remaining_balance`, `status` | unique `po_number`; FK to vendor; checks: amount > 0, tax ≥ 0, total = amount + tax, 0 ≤ balance ≤ total, valid status; indexes on (`vendor_id`, `status`), `status`, `order_date` |
| `invoices` | `invoice_number`, `vendor_id`, `purchase_order_id`, dates, amounts, `reconciliation_status`, `reconciliation_notes`, `source` | unique (`vendor_id`, `invoice_number`); FKs to vendor and PO; checks: amount > 0, tax ≥ 0, total > 0, due ≥ invoice date, valid status; indexes on status, (`purchase_order_id`, status), `invoice_date`, `due_date` |
| `upload_batches` | `idempotency_key`, `file_name`, `file_hash`, `status`, counters | unique `idempotency_key`; status check; indexes on `status`, `created_at`, `file_hash` |
| `upload_records` | `batch_id`, `row_number`, `classification`, `errors`, `raw_data`, `invoice_id` | unique (`batch_id`, `row_number`); FKs to batch and invoice; indexes on (`batch_id`, `classification`), `classification` |

Money is `NUMERIC(14, 2)` and handled as `Decimal` throughout. Foreign keys to
vendors and POs use `PROTECT`, so history cannot be orphaned.

## Tests

```bash
pytest
```

By default the suite runs on in-memory SQLite and needs no running services
(225 tests, about 10 seconds). To run it against PostgreSQL, including the
three concurrency tests (228 tests):

```bash
# Windows PowerShell:  $env:TEST_USE_POSTGRES = "1"; pytest
TEST_USE_POSTGRES=1 pytest
```

In PostgreSQL mode pytest-django creates a `test_<DB_NAME>` database by running
the real migrations, and the FastAPI tests run against that migrated schema.
The database user therefore needs permission to create databases.

| Location | What it covers |
|---|---|
| `tests/unit/test_rules.py` | Every reconciliation rule and the precedence between classifications |
| `tests/unit/test_row_schema.py` | Pydantic validation of upload rows: dates, amounts, required fields |
| `tests/unit/test_file_parser.py` | CSV/XLSX parsing, header aliases, file-level errors |
| `tests/django_api/` | CRUD, validation, filtering, pagination, balance maintenance and database constraints for vendors, POs and invoices |
| `tests/fastapi_api/test_bulk_upload.py` | Classification of uploaded rows, balance tracking, file errors, limits |
| `tests/fastapi_api/test_idempotency.py` | Replay, key reuse, force, in-progress, crash rollback and retry |
| `tests/fastapi_api/test_reconciliation.py` | Reconciling stored invoices, re-runs, filters |
| `tests/fastapi_api/test_reports_and_history.py` | History, the three reports, pagination and filters |
| `tests/fastapi_api/test_concurrency.py` | Parallel reconciliations and uploads (PostgreSQL only) |
| `tests/test_schema_parity.py` | Django models and SQLAlchemy models describe the same tables |

## Project structure

```
├── manage.py
├── config/                     Django project (settings, urls, test settings)
├── apps/                       Django apps
│   ├── common/                 pagination, error handling, seed_demo_data command
│   ├── vendors/                model, serializer, filters, viewset, migrations
│   ├── purchase_orders/
│   ├── invoices/               + services.py (returns PO balance on edit/delete)
│   └── processing/             upload_batches / upload_records models + migrations
├── fastapi_app/                FastAPI service
│   ├── main.py  config.py  database.py  exceptions.py
│   ├── models.py               SQLAlchemy mappings of the Django tables
│   ├── schemas.py              Pydantic: upload-row validation and API contracts
│   ├── routers/                uploads, reconciliation, reports
│   └── services/               file_parser, bulk_upload, reconciliation, reports
├── shared/                     enums.py, rules.py (pure reconciliation rule engine)
├── tests/
├── samples/                    sample_invoices.csv, sample_invoices.xlsx
├── scripts/generate_samples.py
├── docs/                       schema.sql, postman_collection.json, OpenAPI specs
├── Dockerfile  docker-compose.yml
├── requirements.txt  pytest.ini  .env.example
```

## Assumptions and limitations

* **No authentication.** The brief does not ask for it, so both APIs are open.
  In production they would sit behind token authentication.
* **A duplicate means the same vendor and invoice number.** Duplicates are
  rejected rather than stored, so `DUPLICATE` appears on upload records, not on
  stored invoices.
* **Invoices against the wrong PO are stored, not rejected.** The Django API
  accepts an invoice whose PO belongs to another vendor or lacks balance, and
  reconciliation classifies it `MISMATCHED`. Rejecting it up front would leave
  nothing to reconcile.
* **Single currency.** Amounts have no currency column.
* **Uploads are processed synchronously** within the request, which is adequate
  for the 5,000-row limit. Larger volumes would call for a background worker;
  the batch table already has the `PROCESSING` state for that.
* **`.xls` (legacy Excel) is not supported** - only `.xlsx` and `.csv`.
* **Slash- or dash-separated numeric dates are read day-first**
  (`05/01/2026` is 5 January). ISO `YYYY-MM-DD` is unambiguous and recommended.
* **The Docker setup runs development servers** (`runserver`, single `uvicorn`
  process); it is meant for evaluation, not production.
