from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Outcome(str, Enum):
    APPROVE = "APPROVE"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


class LineItem(BaseModel):
    item_code: str | None = None
    description: str = ""
    hsn_sac: str | None = None
    qty: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    line_amount: float | None = None


class Invoice(BaseModel):
    source_file: str = ""
    vendor_name: str | None = None
    vendor_gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    po_number: str | None = None
    payment_terms: str | None = None
    currency: str = "INR"
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: float | None = None
    cgst: float | None = None
    sgst: float | None = None
    igst: float | None = None
    tax_amount: float | None = None
    total: float | None = None
    bank_account_no: str | None = None
    ifsc: str | None = None
    raw_text: str = ""
    extraction_method: str = "heuristic"
    missing_fields: list[str] = Field(default_factory=list)

    def required_missing(self) -> list[str]:
        missing: list[str] = []
        if not self.invoice_number:
            missing.append("invoice_number")
        if not self.invoice_date:
            missing.append("invoice_date")
        if not self.vendor_gstin and not self.vendor_name:
            missing.append("vendor")
        if not self.po_number:
            missing.append("po_number")
        if not self.line_items:
            missing.append("line_items")
        if self.subtotal is None:
            missing.append("subtotal")
        if self.tax_amount is None and self.cgst is None and self.igst is None:
            missing.append("tax")
        if self.total is None:
            missing.append("total")
        return missing


class CheckResult(BaseModel):
    code: str
    passed: bool
    severity: str = "error"  # error | warning
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class LineMatch(BaseModel):
    po_line_no: int | None = None
    item_code: str | None = None
    qty: float
    unit_price: float | None = None


class Decision(BaseModel):
    source_file: str
    outcome: Outcome
    invoice: Invoice
    vendor_id: str | None = None
    po_number: str | None = None
    checks: list[CheckResult] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    line_matches: list[LineMatch] = Field(default_factory=list)
