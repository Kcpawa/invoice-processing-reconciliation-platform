"""Domain enumerations. Single source of truth for both services."""
from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value

    @classmethod
    def values(cls) -> list[str]:
        return [member.value for member in cls]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """Django-style choices."""
        return [(m.value, m.value.replace("_", " ").title()) for m in cls]


class VendorStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class POStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ReconciliationStatus(StrEnum):
    """Status of a persisted invoice.

    There is no DUPLICATE here on purpose: a duplicate (same vendor + invoice
    number) is rejected before it is stored, so it only ever appears as the
    classification of an upload row.
    """

    PENDING = "PENDING"
    MATCHED = "MATCHED"
    MISMATCHED = "MISMATCHED"
    FAILED = "FAILED"


class RecordClassification(StrEnum):
    """Outcome of the reconciliation rules / of one row of a bulk upload."""

    MATCHED = "MATCHED"
    MISMATCHED = "MISMATCHED"
    DUPLICATE = "DUPLICATE"
    FAILED = "FAILED"


class InvoiceSource(StrEnum):
    API = "API"
    BULK_UPLOAD = "BULK_UPLOAD"


class BatchStatus(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
