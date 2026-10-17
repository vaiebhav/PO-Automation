from __future__ import annotations

from src.agent import InvoiceTools, run_agent_on_invoice
from src.config import DEFAULT_DATA, DEFAULT_INVOICES
from src.extract.llm import merge_llm_invoice
from src.extract.parse import parse_invoice_text
from src.master_data import MasterData
from src.models import Outcome


def test_llm_merge_fills_missing_po_only():
    invoice = parse_invoice_text("TAX INVOICE\nInvoice No: X-1\nTOTAL (INR) 10.00")
    invoice.invoice_number = "X-1"
    filled = merge_llm_invoice(
        invoice,
        {
            "po_number": "PO: 2026/1014",
            "invoice_date": "2026-10-16",
            "vendor_gstin": "36AAHCS1357T1Z4",
            "subtotal": 215000,
            "tax_amount": 38700,
            "total": 253700,
            "line_items": [
                {
                    "description": "Video production - 60 sec launch film",
                    "hsn_sac": "998361",
                    "qty": 1,
                    "unit_price": 180000,
                    "line_amount": 180000,
                }
            ],
        },
    )
    assert filled.po_number == "PO-2026-1014"
    assert filled.vendor_gstin == "36AAHCS1357T1Z4"
    assert filled.extraction_method == "heuristic+llm"


def test_agent_falls_back_without_api_key(monkeypatch):
    monkeypatch.setattr("src.agent.GOOGLE_API_KEY", "")
    master = MasterData(DEFAULT_DATA)
    path = DEFAULT_INVOICES / "INV-12_Brightline_happy-pantry.pdf"
    decision = run_agent_on_invoice(path, master, use_llm=False)
    assert decision.outcome == Outcome.APPROVE


def test_invoice_tools_lookup():
    master = MasterData(DEFAULT_DATA)
    tools = InvoiceTools(master, use_llm=False)
    vendor = tools.lookup_vendor(gstin="29AABCN1234F1Z5")
    assert vendor["vendor_id"] == "V001"
    po = tools.lookup_po("PO-2026-1001")
    assert po["found"] is True
    assert len(po["line_items"]) == 2
