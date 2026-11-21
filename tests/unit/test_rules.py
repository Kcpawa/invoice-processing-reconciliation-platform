"""The reconciliation rule engine is pure Python - no database needed."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from shared.enums import RecordClassification as C
from shared.rules import (
    InvoiceCandidate,
    POSnapshot,
    VendorSnapshot,
    evaluate,
    money,
    normalize_code,
)

TODAY = date(2026, 6, 15)


def vendor(**kw):
    return VendorSnapshot(**{"id": 1, "vendor_code": "V001", "status": "ACTIVE", **kw})


def po(**kw):
    defaults = {
        "id": 10, "po_number": "PO-001", "vendor_id": 1, "status": "OPEN",
        "order_date": date(2026, 5, 1), "remaining_balance": Decimal("5000.00"),
    }
    return POSnapshot(**{**defaults, **kw})


def invoice(**kw):
    defaults = {
        "invoice_number": "INV-1", "invoice_date": date(2026, 6, 1),
        "due_date": date(2026, 7, 1), "amount": Decimal("1000.00"),
        "tax_amount": Decimal("180.00"), "total_amount": Decimal("1180.00"),
    }
    return InvoiceCandidate(**{**defaults, **kw})


def codes(result):
    return [reason.code for reason in result.reasons]


def run(inv=None, v="default", p="default", **kw):
    return evaluate(
        inv or invoice(),
        vendor() if v == "default" else v,
        po() if p == "default" else p,
        today=TODAY,
        **kw,
    )


def test_valid_invoice_is_matched():
    result = run()
    assert result.status == C.MATCHED
    assert result.is_matched
    assert result.reasons == []


def test_invoice_equal_to_remaining_balance_is_matched():
    assert run(p=po(remaining_balance=Decimal("1180.00"))).status == C.MATCHED


@pytest.mark.parametrize(
    "kwargs, expected_code",
    [
        ({"v": None}, "VENDOR_NOT_FOUND"),
        ({"v": vendor(status="INACTIVE")}, "VENDOR_INACTIVE"),
        ({"p": None}, "PO_NOT_FOUND"),
        ({"p": po(status="CANCELLED")}, "PO_CANCELLED"),
        ({"inv": invoice(invoice_date=TODAY + timedelta(days=1),
                         due_date=TODAY + timedelta(days=30))}, "INVOICE_DATE_IN_FUTURE"),
        ({"inv": invoice(due_date=date(2026, 5, 31))}, "DUE_DATE_BEFORE_INVOICE_DATE"),
        ({"inv": invoice(amount=Decimal("0"), total_amount=Decimal("180.00"))}, "INVALID_AMOUNT"),
        ({"inv": invoice(tax_amount=Decimal("-1"), total_amount=Decimal("999.00"))}, "INVALID_TAX"),
        ({"inv": invoice(total_amount=Decimal("1200.00"))}, "TOTAL_MISMATCH"),
    ],
)
def test_invalid_invoices_fail(kwargs, expected_code):
    result = run(**kwargs)
    assert result.status == C.FAILED
    assert expected_code in codes(result)


def test_invoice_dated_today_is_allowed():
    inv = invoice(invoice_date=TODAY, due_date=TODAY)
    assert run(inv=inv).status == C.MATCHED


def test_total_within_rounding_tolerance_is_accepted():
    assert run(inv=invoice(total_amount=Decimal("1180.01"))).status == C.MATCHED


def test_all_failure_reasons_are_reported_together():
    result = run(inv=invoice(due_date=date(2026, 5, 1), total_amount=Decimal("1.00")), v=None)
    assert result.status == C.FAILED
    assert {"VENDOR_NOT_FOUND", "DUE_DATE_BEFORE_INVOICE_DATE", "TOTAL_MISMATCH"} <= set(
        codes(result)
    )


@pytest.mark.parametrize(
    "purchase_order, expected_code",
    [
        (po(vendor_id=2), "VENDOR_PO_MISMATCH"),
        (po(status="CLOSED"), "PO_CLOSED"),
        (po(order_date=date(2026, 6, 10)), "INVOICE_BEFORE_PO_DATE"),
        (po(remaining_balance=Decimal("1179.99")), "EXCEEDS_PO_BALANCE"),
    ],
)
def test_po_disagreements_are_mismatched(purchase_order, expected_code):
    result = run(p=purchase_order)
    assert result.status == C.MISMATCHED
    assert expected_code in codes(result)


def test_multiple_mismatch_reasons_are_reported_together():
    result = run(p=po(vendor_id=2, remaining_balance=Decimal("10.00")))
    assert codes(result) == ["VENDOR_PO_MISMATCH", "EXCEEDS_PO_BALANCE"]


def test_duplicate_takes_precedence_over_everything():
    result = run(v=None, p=None, duplicate_reason="Already received.")
    assert result.status == C.DUPLICATE
    assert codes(result) == ["DUPLICATE_INVOICE"]
    assert result.reasons[0].message == "Already received."


def test_failure_takes_precedence_over_mismatch():
    result = run(v=vendor(status="INACTIVE"), p=po(remaining_balance=Decimal("1.00")))
    assert result.status == C.FAILED
    assert "EXCEEDS_PO_BALANCE" not in codes(result)


def test_reasons_serialise_to_dicts():
    result = run(p=po(status="CLOSED"))
    assert result.reasons_as_dicts() == [
        {"code": "PO_CLOSED", "message": "Purchase order PO-001 is closed."}
    ]


@pytest.mark.parametrize(
    "raw, expected",
    [("  inv-001 ", "INV-001"), ("po  12\tab", "PO 12 AB"), ("V001", "V001")],
)
def test_normalize_code(raw, expected):
    assert normalize_code(raw) == expected


def test_money_quantizes_floats_safely():
    assert money(0.1 + 0.2) == Decimal("0.30")
    assert money("1180") == Decimal("1180.00")
