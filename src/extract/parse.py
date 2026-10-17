from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from dateutil import parser as date_parser

from src.models import Invoice, LineItem

GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9])\b")
INVOICE_NO_RE = re.compile(
    r"Invoice\s*No\.?\s*[:#]?\s*([A-Z0-9][A-Z0-9/\-]+)",
    re.IGNORECASE,
)
PAYMENT_TERMS_RE = re.compile(r"Payment\s*terms?\s*[:#]?\s*(.+)", re.IGNORECASE)
INVOICE_DATE_RE = re.compile(
    r"Invoice\s*Date\s*[:#]?\s*(.+)",
    re.IGNORECASE,
)
ACCOUNT_RE = re.compile(r"A/?c\s*No\.?\s*[:#]?\s*([0-9]+)", re.IGNORECASE)
IFSC_RE = re.compile(r"IFSC\s*[:#]?\s*([A-Z]{4}0[A-Z0-9]{6})", re.IGNORECASE)
AMOUNT_TOKEN = r"[\d,]+\.\d{2}"
SUBTOTAL_RE = re.compile(rf"Sub[-\s]?total\s+({AMOUNT_TOKEN})", re.IGNORECASE)
CGST_RE = re.compile(rf"CGST[^\n]*?({AMOUNT_TOKEN})", re.IGNORECASE)
SGST_RE = re.compile(rf"SGST[^\n]*?({AMOUNT_TOKEN})", re.IGNORECASE)
IGST_RE = re.compile(rf"IGST[^\n]*?({AMOUNT_TOKEN})", re.IGNORECASE)
TOTAL_RE = re.compile(rf"TOTAL\s*\(INR\)\s*({AMOUNT_TOKEN})", re.IGNORECASE)
LINE_RE = re.compile(
    rf"^(?:(?P<item>[A-Z][A-Z0-9][A-Z0-9\-/]{{2,}})\s+)?"
    rf"(?P<desc>.+?)\s+"
    rf"(?P<hsn>\d{{4,8}})\s+"
    rf"(?P<qty>\d+(?:\.\d+)?)\s+"
    rf"(?:(?P<unit>[A-Za-z][A-Za-z\-]+)\s+)?"
    rf"(?P<rate>{AMOUNT_TOKEN})\s+"
    rf"(?P<amount>{AMOUNT_TOKEN})\s*$"
)
SKIP_LINE_RE = re.compile(
    r"^(item\s*code|description|sub[-\s]?total|cgst|sgst|igst|total|bill to|"
    r"bank details|this is a computer|gstin:|hsn)",
    re.IGNORECASE,
)
PO_CANDIDATE_RE = re.compile(
    r"(?:PO\s*(?:Number|No\.?|Ref(?:erence)?|Reference)|Your\s+Order\s+Ref)"
    r"\s*[:.]?\s*(.+)",
    re.IGNORECASE,
)
PO_NORMALIZE_RE = re.compile(r"(\d{4})\D+(\d{3,})", re.IGNORECASE)


def parse_amount(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace(",", "").replace("INR", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip().split("\n")[0].strip()
    text = re.split(r"\s{2,}|Payment", text, maxsplit=1)[0].strip()
    try:
        return date_parser.parse(text, dayfirst=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def normalize_po(raw: str | None) -> str | None:
    if not raw:
        return None
    compact = re.sub(r"\s+", " ", raw).strip()
    compact = compact.split("GSTIN")[0].strip()
    match = PO_NORMALIZE_RE.search(compact)
    if not match:
        dashed = re.search(r"PO[-\s]*\d{4}[-\s/]\d+", compact, re.IGNORECASE)
        if dashed:
            match = PO_NORMALIZE_RE.search(dashed.group(0))
    if not match:
        return None
    year, number = match.group(1), match.group(2)
    return f"PO-{year}-{number}"


def _header_vendor_name(text: str) -> str | None:
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    if not first:
        return None
    first = re.sub(r"\s+TAX\s+INVOICE\s*$", "", first, flags=re.IGNORECASE).strip()
    first = re.sub(r"^TAX\s+INVOICE\s+", "", first, flags=re.IGNORECASE).strip()
    return first or None


def _lines_from_tables(tables: list) -> list[LineItem]:
    items: list[LineItem] = []
    for table in tables:
        if not table:
            continue
        header = [re.sub(r"\s+", " ", (c or "")).strip().lower() for c in table[0]]
        def col(*names: str) -> int | None:
            for i, h in enumerate(header):
                for name in names:
                    if name in h:
                        return i
            return None

        desc_i = col("description")
        amt_i = col("amount")
        qty_i = col("qty", "quantity")
        rate_i = col("rate", "unit price")
        hsn_i = col("hsn", "sac")
        code_i = col("item code", "sku")
        unit_i = col("unit")
        if desc_i is None or amt_i is None:
            continue
        for row in table[1:]:
            cells = [(c or "").strip() for c in row]
            if not any(cells):
                continue
            joined = " ".join(cells).lower()
            if joined.startswith("sub") or "total" in cells[0].lower():
                continue
            desc = cells[desc_i] if desc_i < len(cells) else ""
            amount = parse_amount(cells[amt_i] if amt_i < len(cells) else None)
            if not desc or amount is None:
                continue
            items.append(
                LineItem(
                    item_code=(
                        cells[code_i]
                        if code_i is not None and code_i < len(cells) and cells[code_i]
                        else None
                    ),
                    description=desc,
                    hsn_sac=(
                        re.sub(r"\s+", "", cells[hsn_i])
                        if hsn_i is not None and hsn_i < len(cells)
                        else None
                    )
                    or None,
                    qty=parse_amount(cells[qty_i] if qty_i is not None and qty_i < len(cells) else None),
                    unit=(
                        cells[unit_i]
                        if unit_i is not None and unit_i < len(cells) and cells[unit_i]
                        else None
                    ),
                    unit_price=parse_amount(
                        cells[rate_i] if rate_i is not None and rate_i < len(cells) else None
                    ),
                    line_amount=amount,
                )
            )
    return items


def _lines_from_text(text: str) -> list[LineItem]:
    items: list[LineItem] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or SKIP_LINE_RE.match(line):
            continue
        match = LINE_RE.match(line)
        if not match:
            continue
        items.append(
            LineItem(
                item_code=match.group("item"),
                description=match.group("desc").strip(" -"),
                hsn_sac=match.group("hsn"),
                qty=parse_amount(match.group("qty")),
                unit=match.group("unit"),
                unit_price=parse_amount(match.group("rate")),
                line_amount=parse_amount(match.group("amount")),
            )
        )
    return items


def parse_invoice_text(
    text: str,
    tables: list | None = None,
    source_file: str = "",
) -> Invoice:
    gstins = GSTIN_RE.findall(text)
    vendor_gstin = gstins[0] if gstins else None
    po_raw = None
    for line in text.splitlines():
        po_match = PO_CANDIDATE_RE.search(line)
        if po_match:
            po_raw = po_match.group(1)
            break
    invoice_no = None
    inv_match = INVOICE_NO_RE.search(text)
    if inv_match:
        invoice_no = inv_match.group(1).strip()

    inv_date = None
    date_match = INVOICE_DATE_RE.search(text)
    if date_match:
        inv_date = parse_date(date_match.group(1))

    terms = None
    terms_match = PAYMENT_TERMS_RE.search(text)
    if terms_match:
        terms = terms_match.group(1).strip()

    subtotal = parse_amount(m.group(1) if (m := SUBTOTAL_RE.search(text)) else None)
    cgst = parse_amount(m.group(1) if (m := CGST_RE.search(text)) else None)
    sgst = parse_amount(m.group(1) if (m := SGST_RE.search(text)) else None)
    igst = parse_amount(m.group(1) if (m := IGST_RE.search(text)) else None)
    total = parse_amount(m.group(1) if (m := TOTAL_RE.search(text)) else None)
    tax_amount = None
    if igst is not None:
        tax_amount = igst
    elif cgst is not None or sgst is not None:
        tax_amount = (cgst or 0) + (sgst or 0)

    table_lines = _lines_from_tables(tables or [])
    text_lines = _lines_from_text(text)
    line_items = table_lines if table_lines else text_lines

    account = m.group(1) if (m := ACCOUNT_RE.search(text)) else None
    ifsc = m.group(1) if (m := IFSC_RE.search(text)) else None

    invoice = Invoice(
        source_file=Path(source_file).name if source_file else "",
        vendor_name=_header_vendor_name(text),
        vendor_gstin=vendor_gstin,
        invoice_number=invoice_no,
        invoice_date=inv_date,
        po_number=normalize_po(po_raw),
        payment_terms=terms,
        line_items=line_items,
        subtotal=subtotal,
        cgst=cgst,
        sgst=sgst,
        igst=igst,
        tax_amount=tax_amount,
        total=total,
        bank_account_no=account,
        ifsc=ifsc,
        raw_text=text,
        extraction_method="heuristic",
    )
    invoice.missing_fields = invoice.required_missing()
    return invoice
