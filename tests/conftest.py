"""Shared fixtures.

Django API tests use pytest-django. FastAPI tests get a SQLAlchemy engine:

* default            - in-memory SQLite, schema built from the SQLAlchemy models
* TEST_USE_POSTGRES=1 - the PostgreSQL test database that pytest-django
                        creates by running the real Django migrations
"""
import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fastapi_app.config import Settings, get_settings
from fastapi_app.database import get_db
from fastapi_app.main import create_app
from fastapi_app.models import Base, Invoice, PurchaseOrder, Vendor
from tests.helpers import DUE_DATE, INVOICE_DATE, PO_DATE

USE_POSTGRES = os.getenv("TEST_USE_POSTGRES", "").lower() in {"1", "true", "yes"}
ALL_TABLES = "upload_records, upload_batches, invoices, purchase_orders, vendors"


def pytest_collection_modifyitems(items):
    """pytest-django only creates the test database when some collected test
    is marked as needing it, so flag the SQLAlchemy tests too on PostgreSQL."""
    if not USE_POSTGRES:
        return
    for item in items:
        if "engine" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.django_db)


def _sqlite_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    # pysqlite's implicit transaction handling breaks SAVEPOINTs; take over
    # BEGIN ourselves (the recipe from the SQLAlchemy documentation).
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def _on_begin(connection):
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def engine(request):
    if not USE_POSTGRES:
        engine = _sqlite_engine()
        yield engine
        engine.dispose()
        return

    request.getfixturevalue("django_db_setup")  # creates + migrates the test DB
    from django.conf import settings as django_settings

    db = django_settings.DATABASES["default"]
    engine = create_engine(
        URL.create(
            "postgresql+psycopg", username=db["USER"], password=db["PASSWORD"],
            host=db["HOST"], port=int(db["PORT"]), database=db["NAME"],
        )
    )
    yield engine
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {ALL_TABLES} RESTART IDENTITY CASCADE"))
    engine.dispose()


@pytest.fixture
def sa_session(engine):
    """The session used both by the test and by the app under test."""
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def app_settings():
    return Settings(_env_file=None)


@pytest.fixture
def client(sa_session, app_settings):
    app = create_app()

    def override_get_db():
        try:
            yield sa_session
        finally:
            sa_session.rollback()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: app_settings
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# ---------------------------------------------------------------- factories
@pytest.fixture
def make_vendor(sa_session: Session):
    def _make(vendor_code="V001", name=None, status="ACTIVE") -> Vendor:
        vendor = Vendor(vendor_code=vendor_code, name=name or f"Vendor {vendor_code}",
                        status=status)
        sa_session.add(vendor)
        sa_session.commit()
        return vendor

    return _make


@pytest.fixture
def make_po(sa_session: Session):
    def _make(vendor: Vendor, po_number="PO-001", amount="10000.00", tax="0.00",
              status="OPEN", order_date=PO_DATE) -> PurchaseOrder:
        total = Decimal(amount) + Decimal(tax)
        po = PurchaseOrder(
            po_number=po_number, vendor_id=vendor.id, order_date=order_date,
            amount=Decimal(amount), tax_amount=Decimal(tax), total_amount=total,
            remaining_balance=total, status=status,
        )
        sa_session.add(po)
        sa_session.commit()
        return po

    return _make


@pytest.fixture
def make_invoice(sa_session: Session):
    def _make(vendor: Vendor, po: PurchaseOrder, invoice_number="INV-001",
              amount="1000.00", tax="0.00", status="PENDING",
              invoice_date=INVOICE_DATE, due_date=DUE_DATE) -> Invoice:
        invoice = Invoice(
            invoice_number=invoice_number, vendor_id=vendor.id, purchase_order_id=po.id,
            invoice_date=invoice_date, due_date=due_date, amount=Decimal(amount),
            tax_amount=Decimal(tax), total_amount=Decimal(amount) + Decimal(tax),
            reconciliation_status=status,
        )
        sa_session.add(invoice)
        sa_session.commit()
        return invoice

    return _make


@pytest.fixture
def upload(client):
    """POST a file to the bulk-upload endpoint."""

    def _upload(content: bytes, filename="invoices.csv", key=None, **params):
        headers = {"Idempotency-Key": key} if key else {}
        return client.post(
            "/api/v1/uploads/invoices",
            files={"file": (filename, content, "application/octet-stream")},
            headers=headers,
            params=params,
        )

    return _upload
