"""SQLAlchemy mappings of the tables created by the Django migrations.

Django owns the schema; these classes must mirror it column-for-column
(tests/test_schema_parity.py enforces that). Django sets defaults and
timestamps in Python rather than in the database, so every default here is
a client-side default as well.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from shared.enums import (
    BatchStatus,
    InvoiceSource,
    POStatus,
    ReconciliationStatus,
    VendorStatus,
)

# SQLite (used by the test-suite) only auto-increments INTEGER primary keys.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")
JSONType = JSON().with_variant(JSONB(), "postgresql")
Money = Numeric(14, 2)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """timestamptz column that always yields timezone-aware datetimes.

    PostgreSQL already does; SQLite (tests) drops the offset, so re-attach UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

class Base(DeclarativeBase):
    pass


class Vendor(Base):
    __tablename__ = "vendors"
    __table_args__ = (
        Index("idx_vendor_status", "status"),
        Index("idx_vendor_name", "name"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    vendor_code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(254), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    tax_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    address: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default=VendorStatus.ACTIVE.value)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_po_amount_positive"),
        CheckConstraint("tax_amount >= 0", name="ck_po_tax_non_negative"),
        CheckConstraint(
            "remaining_balance >= 0 AND remaining_balance <= total_amount",
            name="ck_po_balance_within_total",
        ),
        Index("idx_po_vendor_status", "vendor_id", "status"),
        Index("idx_po_status", "status"),
        Index("idx_po_order_date", "order_date"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    po_number: Mapped[str] = mapped_column(String(50), unique=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"))
    description: Mapped[str] = mapped_column(String(500), default="")
    order_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    tax_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Money)
    remaining_balance: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(10), default=POStatus.OPEN.value)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )

    vendor: Mapped[Vendor] = relationship(lazy="joined", innerjoin=True)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("vendor_id", "invoice_number", name="uq_invoice_vendor_number"),
        CheckConstraint("amount > 0", name="ck_invoice_amount_positive"),
        CheckConstraint("tax_amount >= 0", name="ck_invoice_tax_non_negative"),
        CheckConstraint("total_amount > 0", name="ck_invoice_total_positive"),
        CheckConstraint(
            "due_date >= invoice_date", name="ck_invoice_due_not_before_invoice_date"
        ),
        Index("idx_invoice_recon_status", "reconciliation_status"),
        Index("idx_invoice_po_status", "purchase_order_id", "reconciliation_status"),
        Index("idx_invoice_date", "invoice_date"),
        Index("idx_invoice_due_date", "due_date"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(50))
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"))
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    tax_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Money)
    reconciliation_status: Mapped[str] = mapped_column(
        String(12), default=ReconciliationStatus.PENDING.value
    )
    reconciliation_notes: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    reconciled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    source: Mapped[str] = mapped_column(String(12), default=InvoiceSource.API.value)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )

    vendor: Mapped[Vendor] = relationship(lazy="joined", innerjoin=True)
    purchase_order: Mapped[PurchaseOrder] = relationship(lazy="joined", innerjoin=True)


class UploadBatch(Base):
    __tablename__ = "upload_batches"
    __table_args__ = (
        Index("idx_batch_status", "status"),
        Index("idx_batch_created_at", "created_at"),
        Index("idx_batch_file_hash", "file_hash"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    file_name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(10))
    file_hash: Mapped[str] = mapped_column(String(64))
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default=BatchStatus.PROCESSING.value)
    total_records: Mapped[int] = mapped_column(Integer, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, default=0)
    mismatched_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    @property
    def success_count(self) -> int:
        """Rows imported as invoices (matched + mismatched)."""
        return self.matched_count + self.mismatched_count

    @property
    def failure_count(self) -> int:
        return self.failed_count


class UploadRecord(Base):
    __tablename__ = "upload_records"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_number", name="uq_record_batch_row"),
        Index("idx_record_batch_class", "batch_id", "classification"),
        Index("idx_record_classification", "classification"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("upload_batches.id"))
    row_number: Mapped[int] = mapped_column(Integer)
    invoice_number: Mapped[str] = mapped_column(String(50), default="")
    vendor_code: Mapped[str] = mapped_column(String(30), default="")
    po_number: Mapped[str] = mapped_column(String(50), default="")
    classification: Mapped[str] = mapped_column(String(12))
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
