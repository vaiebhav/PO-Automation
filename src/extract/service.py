from __future__ import annotations

from pathlib import Path

from src.extract.llm import apply_llm_fallback
from src.extract.parse import parse_invoice_text
from src.extract.pdf import extract_pdf
from src.models import Invoice


def extract_invoice(path: Path, use_llm: bool = True) -> Invoice:
    raw = extract_pdf(path)
    invoice = parse_invoice_text(
        raw["text"],
        tables=raw["tables"],
        source_file=str(path),
    )
    if use_llm and invoice.required_missing():
        invoice = apply_llm_fallback(invoice)
    return invoice
