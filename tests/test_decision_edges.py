from __future__ import annotations

from copy import deepcopy
from datetime import date

from src.config import DEFAULT_DATA, DEFAULT_INVOICES
from src.extract.service import extract_invoice
from src.master_data import MasterData
from src.models import Invoice, LineItem, Outcome
from src.pipeline import process_invoice
from src.validate import validate_invoice


def _vertex_invoice() -> Invoice:
    return extract_invoice(
        DEFAULT_INVOICES / "INV-13_Vertex_happy-monitors.pdf",
        use_llm=False,
    )


def test_partial_po_draw_approves():
    master = MasterData(DEFAULT_DATA)
    invoice = _vertex_invoice()
    invoice.line_items = [
        LineItem(
            item_code="VIT-MON-27",
            description="27-inch IPS monitor 2560x1440",
            hsn_sac="8528",
            qty=5,
            unit_price=14500,
            line_amount=72500,
        )
    ]
    invoice.subtotal = 72500
    invoice.cgst = 6525
    invoice.sgst = 6525
    invoice.tax_amount = 13050
    invoice.total = 85550
    decision = validate_invoice(invoice, master)
    assert decision.outcome == Outcome.APPROVE, decision.reasons
    assert decision.checks and any(c.code == "LINE_ITEMS" and c.passed for c in decision.checks)


def test_over_remaining_balance_is_review():
    master = MasterData(DEFAULT_DATA)
    invoice = _vertex_invoice()
    master.purchase_orders.loc[
        master.purchase_orders["po_number"] == "PO-2026-1012",
        "remaining_balance",
    ] = 100.0
    decision = validate_invoice(invoice, master)
    assert decision.outcome == Outcome.REVIEW
    assert any("exceeds PO remaining" in r for r in decision.reasons)


def test_duplicate_in_same_run_is_reject():
    master = MasterData(DEFAULT_DATA)
    path = DEFAULT_INVOICES / "INV-01_Nimbus_happy-path.pdf"
    first = process_invoice(path, master, use_llm=False)
    second = process_invoice(path, master, use_llm=False)
    assert first.outcome == Outcome.APPROVE
    assert second.outcome == Outcome.REJECT
    assert any("Duplicate" in r for r in second.reasons)


def test_unapproved_vendor_is_reject():
    master = MasterData(DEFAULT_DATA)
    master.vendors.loc[master.vendors["vendor_id"] == "V005", "approval_status"] = "Pending"
    decision = validate_invoice(_vertex_invoice(), master)
    assert decision.outcome == Outcome.REJECT
    assert any("not approved" in r.lower() for r in decision.reasons)


def test_missing_fields_is_review():
    master = MasterData(DEFAULT_DATA)
    invoice = Invoice(
        source_file="blank.pdf",
        vendor_name="Vertex IT Hardware",
        vendor_gstin="29AAGCV2468R1Z7",
        invoice_date=date(2026, 10, 15),
        po_number="PO-2026-1012",
    )
    decision = validate_invoice(invoice, master)
    assert decision.outcome == Outcome.REVIEW
    assert any("Incomplete extraction" in r for r in decision.reasons)


def test_second_partial_sees_reduced_remaining():
    master = MasterData(DEFAULT_DATA)
    first = _vertex_invoice()
    first.line_items = [
        LineItem(
            item_code="VIT-MON-27",
            description="27-inch IPS monitor 2560x1440",
            hsn_sac="8528",
            qty=10,
            unit_price=14500,
            line_amount=145000,
        )
    ]
    first.subtotal = 145000
    first.tax_amount = 26100
    first.cgst = 13050
    first.sgst = 13050
    first.total = 171100
    first.invoice_number = "VIT-PARTIAL-1"
    d1 = validate_invoice(first, master)
    assert d1.outcome == Outcome.APPROVE, d1.reasons
    master.record_processed(d1)

    second = deepcopy(first)
    second.invoice_number = "VIT-PARTIAL-2"
    second.line_items = [
        LineItem(
            item_code="VIT-MON-27",
            description="27-inch IPS monitor 2560x1440",
            hsn_sac="8528",
            qty=10,
            unit_price=14500,
            line_amount=145000,
        )
    ]
    d2 = validate_invoice(second, master)
    assert d2.outcome == Outcome.REVIEW
    assert any(c.code == "LINE_ITEMS" and not c.passed for c in d2.checks)
