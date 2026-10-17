from __future__ import annotations

from pathlib import Path

from src.config import DEFAULT_DATA, DEFAULT_INVOICES
from src.extract.parse import normalize_po, parse_amount, parse_date
from src.extract.service import extract_invoice
from src.master_data import MasterData
from src.models import Outcome
from src.pipeline import compare_expected, process_folder


def test_normalize_po_variants():
    assert normalize_po("PO-2026-1001") == "PO-2026-1001"
    assert normalize_po("PO 2026-1012") == "PO-2026-1012"
    assert normalize_po("PO: 2026/1014") == "PO-2026-1014"


def test_parse_indian_and_western_amounts():
    assert parse_amount("1,00,300.00") == 100300.00
    assert parse_amount("343,380.00") == 343380.00
    assert parse_amount("26,691.60") == 26691.60


def test_parse_dates():
    assert parse_date("12 Oct 2026").isoformat() == "2026-10-12"
    assert parse_date("2026-10-13").isoformat() == "2026-10-13"
    assert parse_date("14/10/2026").isoformat() == "2026-10-14"
    assert parse_date("October 16, 2026").isoformat() == "2026-10-16"


def test_heuristic_extracts_required_fields():
    path = DEFAULT_INVOICES / "INV-01_Nimbus_happy-path.pdf"
    invoice = extract_invoice(path, use_llm=False)
    assert invoice.required_missing() == []
    assert invoice.vendor_gstin == "29AABCN1234F1Z5"
    assert invoice.invoice_number == "INV-NCS-2026-0457"
    assert invoice.po_number == "PO-2026-1001"
    assert invoice.total == 343380.00
    assert len(invoice.line_items) == 2


def test_happy_path_all_approve():
    master = MasterData(DEFAULT_DATA)
    decisions = process_folder(DEFAULT_INVOICES, DEFAULT_DATA, use_llm=False)
    assert len(decisions) == 7
    summary = compare_expected(decisions, master)
    failed = [row for row in summary if not row["match"]]
    assert not failed, failed
    assert all(d.outcome == Outcome.APPROVE for d in decisions)


def test_sundial_po_slash_format():
    invoice = extract_invoice(
        DEFAULT_INVOICES / "INV-15_Sundial_happy-launch-video.pdf",
        use_llm=False,
    )
    assert invoice.po_number == "PO-2026-1014"
    assert invoice.igst == 38700.00
