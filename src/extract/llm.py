from __future__ import annotations

import json

from src.config import GEMINI_MODEL, GOOGLE_API_KEY
from src.extract.parse import normalize_po, parse_amount, parse_date
from src.models import Invoice, LineItem


INVOICE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor_name": {"type": "string"},
        "vendor_gstin": {"type": "string"},
        "invoice_number": {"type": "string"},
        "invoice_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
        "po_number": {"type": "string"},
        "payment_terms": {"type": "string"},
        "subtotal": {"type": "number"},
        "cgst": {"type": "number"},
        "sgst": {"type": "number"},
        "igst": {"type": "number"},
        "tax_amount": {"type": "number"},
        "total": {"type": "number"},
        "bank_account_no": {"type": "string"},
        "ifsc": {"type": "string"},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_code": {"type": "string"},
                    "description": {"type": "string"},
                    "hsn_sac": {"type": "string"},
                    "qty": {"type": "number"},
                    "unit": {"type": "string"},
                    "unit_price": {"type": "number"},
                    "line_amount": {"type": "number"},
                },
            },
        },
    },
}


def llm_available() -> bool:
    return bool(GOOGLE_API_KEY)


def extract_with_gemini(raw_text: str) -> dict:
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GOOGLE_API_KEY)
    prompt = (
        "Extract invoice fields from this Indian tax invoice text. "
        "Return JSON only. Use numbers without thousand separators. "
        "Normalize the purchase order to PO-YYYY-NNNN if possible.\n\n"
        f"{raw_text}"
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=INVOICE_JSON_SCHEMA,
        ),
    )
    return json.loads(response.text)


def _fill(current, incoming):
    if current in (None, "", []):
        return incoming
    return current


def merge_llm_invoice(invoice: Invoice, payload: dict) -> Invoice:
    data = payload or {}
    invoice.vendor_name = _fill(invoice.vendor_name, data.get("vendor_name") or None)
    invoice.vendor_gstin = _fill(invoice.vendor_gstin, data.get("vendor_gstin") or None)
    invoice.invoice_number = _fill(invoice.invoice_number, data.get("invoice_number") or None)
    if not invoice.invoice_date and data.get("invoice_date"):
        parsed = parse_date(str(data["invoice_date"]))
        invoice.invoice_date = parsed
    invoice.po_number = _fill(invoice.po_number, normalize_po(data.get("po_number")) or data.get("po_number"))
    invoice.payment_terms = _fill(invoice.payment_terms, data.get("payment_terms") or None)
    invoice.subtotal = _fill(invoice.subtotal, parse_amount(data.get("subtotal")))
    invoice.cgst = _fill(invoice.cgst, parse_amount(data.get("cgst")))
    invoice.sgst = _fill(invoice.sgst, parse_amount(data.get("sgst")))
    invoice.igst = _fill(invoice.igst, parse_amount(data.get("igst")))
    invoice.tax_amount = _fill(invoice.tax_amount, parse_amount(data.get("tax_amount")))
    if invoice.tax_amount is None:
        if invoice.igst is not None:
            invoice.tax_amount = invoice.igst
        elif invoice.cgst is not None or invoice.sgst is not None:
            invoice.tax_amount = (invoice.cgst or 0) + (invoice.sgst or 0)
    invoice.total = _fill(invoice.total, parse_amount(data.get("total")))
    invoice.bank_account_no = _fill(invoice.bank_account_no, data.get("bank_account_no") or None)
    invoice.ifsc = _fill(invoice.ifsc, data.get("ifsc") or None)
    if not invoice.line_items and data.get("line_items"):
        invoice.line_items = [LineItem(**item) for item in data["line_items"]]
    invoice.extraction_method = "heuristic+llm"
    invoice.missing_fields = invoice.required_missing()
    return invoice


def apply_llm_fallback(invoice: Invoice) -> Invoice:
    if not invoice.required_missing():
        return invoice
    if not llm_available():
        return invoice
    payload = extract_with_gemini(invoice.raw_text)
    return merge_llm_invoice(invoice, payload)
