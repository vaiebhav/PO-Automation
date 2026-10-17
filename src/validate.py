from __future__ import annotations

from src.config import AMOUNT_TOLERANCE, QTY_TOLERANCE
from src.master_data import MasterData
from src.models import CheckResult, Decision, Invoice, LineMatch, Outcome
from rapidfuzz import fuzz


def _close(a: float | None, b: float | None, tol: float = AMOUNT_TOLERANCE) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def _vendor_match(invoice: Invoice, master: MasterData) -> tuple[dict | None, CheckResult]:
    vendor = None
    if invoice.vendor_gstin:
        vendor = master.vendor_by_gstin(invoice.vendor_gstin)
        if vendor:
            return vendor, CheckResult(
                code="VENDOR_GSTIN",
                passed=True,
                message=f"Vendor GSTIN {invoice.vendor_gstin} matches {vendor['vendor_id']}",
                details={"vendor_id": vendor["vendor_id"]},
            )
        return None, CheckResult(
            code="VENDOR_GSTIN",
            passed=False,
            message=f"GSTIN {invoice.vendor_gstin} is not on the vendor master",
        )

    if invoice.vendor_name:
        best = None
        best_score = 0
        for row in master.vendor_rows():
            score = fuzz.token_set_ratio(invoice.vendor_name, row["vendor_name"])
            if score > best_score:
                best_score = score
                best = row
        if best and best_score >= 85:
            return best, CheckResult(
                code="VENDOR_NAME",
                passed=True,
                severity="warning",
                message=f"Vendor matched by name ({best_score}) to {best['vendor_id']}",
                details={"vendor_id": best["vendor_id"], "score": best_score},
            )
    return None, CheckResult(
        code="VENDOR_IDENTITY",
        passed=False,
        message="Could not match vendor by GSTIN or name",
    )


def _match_line(
    inv_line, po_lines: list[dict], remaining: list[float]
) -> tuple[int | None, str]:
    """Match an invoice line to a PO line. Qty may be a partial draw on remaining qty."""
    candidates: list[tuple[int, int, str]] = []
    inv_qty = float(inv_line.qty or 0)
    for idx, po in enumerate(po_lines):
        if remaining[idx] + QTY_TOLERANCE < inv_qty:
            continue
        price_ok = _close(inv_line.unit_price, float(po["unit_price"]), 0.05)
        if not price_ok:
            continue
        score = 40
        reasons = ["unit_price", "qty<=remaining"]
        if inv_line.hsn_sac and str(po.get("hsn_sac", "")) == str(inv_line.hsn_sac):
            score += 25
            reasons.append("hsn")
        desc_score = fuzz.token_set_ratio(inv_line.description or "", po.get("description") or "")
        score += int(desc_score * 0.25)
        reasons.append(f"desc:{desc_score}")
        if inv_line.item_code and po.get("item_code"):
            inv_code = str(inv_line.item_code).replace("-", "")
            po_code = str(po["item_code"]).replace("-", "")
            if inv_code in po_code or po_code in inv_code:
                score += 15
                reasons.append("item_code")
        candidates.append((score, idx, ",".join(reasons)))
    if not candidates:
        return None, "no price/remaining-qty match"
    candidates.sort(reverse=True)
    return candidates[0][1], candidates[0][2]


def validate_invoice(invoice: Invoice, master: MasterData) -> Decision:
    checks: list[CheckResult] = []
    reasons: list[str] = []
    vendor_id = None
    po_number = invoice.po_number
    line_matches: list[LineMatch] = []

    missing = invoice.required_missing()
    checks.append(
        CheckResult(
            code="REQUIRED_FIELDS",
            passed=not missing,
            message="All required fields present" if not missing else f"Missing: {', '.join(missing)}",
            details={"missing": missing},
        )
    )
    if missing:
        reasons.append(f"Incomplete extraction: {', '.join(missing)}")

    vendor, vendor_check = _vendor_match(invoice, master)
    checks.append(vendor_check)
    if vendor:
        vendor_id = vendor["vendor_id"]
        approved = str(vendor.get("approval_status", "")).lower() == "approved"
        checks.append(
            CheckResult(
                code="VENDOR_APPROVED",
                passed=approved,
                message="Vendor is approved" if approved else "Vendor is not approved",
                details={"approval_status": vendor.get("approval_status")},
            )
        )
        if not approved:
            reasons.append("Vendor is not approved")
        if invoice.vendor_gstin and vendor.get("gstin") and invoice.vendor_gstin != vendor["gstin"]:
            checks.append(
                CheckResult(
                    code="VENDOR_GSTIN_EXACT",
                    passed=False,
                    message="GSTIN does not match vendor master",
                )
            )
            reasons.append("GSTIN mismatch")
    else:
        checks.append(
            CheckResult(
                code="VENDOR_APPROVED",
                passed=False,
                message="Cannot confirm vendor approval; vendor not found",
            )
        )
        reasons.append("Unknown vendor")

    po = master.po_by_number(invoice.po_number or "")
    if not po:
        checks.append(
            CheckResult(
                code="PO_FOUND",
                passed=False,
                message=f"PO {invoice.po_number or '(none)'} not found",
            )
        )
        reasons.append("PO not found")
    else:
        po_number = str(po["po_number"])
        checks.append(
            CheckResult(
                code="PO_FOUND",
                passed=True,
                message=f"Matched {po_number}",
            )
        )
        open_ok = str(po.get("status", "")).lower() == "open"
        checks.append(
            CheckResult(
                code="PO_OPEN",
                passed=open_ok,
                message=f"PO status is {po.get('status')}",
            )
        )
        if not open_ok:
            reasons.append("PO is not open")
        vendor_ok = vendor_id is None or str(po.get("vendor_id")) == str(vendor_id)
        checks.append(
            CheckResult(
                code="PO_VENDOR",
                passed=vendor_ok,
                message="PO vendor matches invoice vendor"
                if vendor_ok
                else "PO belongs to a different vendor",
            )
        )
        if not vendor_ok:
            reasons.append("PO vendor mismatch")

        remaining = float(po.get("remaining_balance") or 0)
        total_ok = invoice.total is not None and invoice.total <= remaining + AMOUNT_TOLERANCE
        checks.append(
            CheckResult(
                code="PO_AMOUNT",
                passed=total_ok,
                message=(
                    f"Invoice total {invoice.total} is within remaining {remaining}"
                    if total_ok
                    else f"Invoice total {invoice.total} exceeds remaining {remaining}"
                ),
                details={"invoice_total": invoice.total, "remaining_balance": remaining},
            )
        )
        if not total_ok:
            reasons.append("Invoice total exceeds PO remaining balance")

        remaining_net = float(po.get("remaining_net") or po.get("net_amount") or 0)
        if invoice.subtotal is not None:
            net_ok = invoice.subtotal <= remaining_net + AMOUNT_TOLERANCE
            checks.append(
                CheckResult(
                    code="PO_NET",
                    passed=net_ok,
                    message=f"Subtotal {invoice.subtotal} vs remaining net {remaining_net}",
                )
            )
            if not net_ok:
                reasons.append("Subtotal exceeds remaining PO net amount")

        gst_rate = float(po.get("gst_rate_pct") or 0)
        tax = invoice.tax_amount
        if tax is None and invoice.subtotal is not None:
            tax = (invoice.total or 0) - invoice.subtotal
        expected_gst = (invoice.subtotal or 0) * gst_rate / 100.0
        gst_ok = invoice.subtotal is not None and _close(tax, expected_gst, 1.0)
        checks.append(
            CheckResult(
                code="GST_MATH",
                passed=gst_ok,
                message=f"Tax {tax} vs expected {expected_gst:.2f} at {gst_rate}% of invoice net",
            )
        )
        if not gst_ok:
            reasons.append("GST amount does not match the invoice net at the PO rate")

        if invoice.subtotal is not None and tax is not None and invoice.total is not None:
            arith_ok = _close(invoice.subtotal + tax, invoice.total, AMOUNT_TOLERANCE)
            checks.append(
                CheckResult(
                    code="INVOICE_ARITHMETIC",
                    passed=arith_ok,
                    message=f"Subtotal {invoice.subtotal} + tax {tax} vs total {invoice.total}",
                )
            )
            if not arith_ok:
                reasons.append("Invoice subtotal plus tax does not equal total")

        po_lines = master.po_lines(po_number)
        remaining_qty = [
            float(p.get("remaining_qty") if p.get("remaining_qty") is not None else p["qty"])
            for p in po_lines
        ]
        unmatched: list[str] = []
        for inv_line in invoice.line_items:
            idx, _how = _match_line(inv_line, po_lines, remaining_qty)
            if idx is None:
                unmatched.append(inv_line.description or "(no description)")
                continue
            qty = float(inv_line.qty or 0)
            remaining_qty[idx] -= qty
            po_line = po_lines[idx]
            line_no = po_line.get("line_no")
            line_matches.append(
                LineMatch(
                    po_line_no=int(line_no) if line_no is not None and str(line_no).isdigit() else None,
                    item_code=str(po_line.get("item_code") or "") or None,
                    qty=qty,
                    unit_price=float(po_line["unit_price"]),
                )
            )
        leftover = [
            po_lines[i]["description"]
            for i, left in enumerate(remaining_qty)
            if left > QTY_TOLERANCE
        ]
        lines_ok = not unmatched
        checks.append(
            CheckResult(
                code="LINE_ITEMS",
                passed=lines_ok,
                message=(
                    "Invoice lines match PO lines (partial draws allowed)"
                    if lines_ok
                    else f"Unmatched invoice lines: {unmatched}"
                ),
                details={"unmatched": unmatched, "unused_po_lines": leftover},
            )
        )
        if not lines_ok:
            reasons.append("Line items do not match the PO")

    # Duplicates
    dup = False
    for row in master.history_rows():
        same_no = str(row.get("invoice_number", "")).strip() == str(invoice.invoice_number or "")
        same_vendor = vendor_id is None or str(row.get("vendor_id", "")) == str(vendor_id)
        if same_no and same_vendor and invoice.invoice_number:
            dup = True
            break
    checks.append(
        CheckResult(
            code="DUPLICATE",
            passed=not dup,
            message="No duplicate in invoice history" if not dup else "Duplicate invoice detected",
        )
    )
    if dup:
        reasons.append("Duplicate invoice")

    if vendor:
        bank_ok = True
        bank_notes = []
        if invoice.bank_account_no and vendor.get("bank_account_no"):
            if invoice.bank_account_no != vendor["bank_account_no"]:
                bank_ok = False
                bank_notes.append("account")
        if invoice.ifsc and vendor.get("ifsc"):
            if invoice.ifsc.upper() != str(vendor["ifsc"]).upper():
                bank_ok = False
                bank_notes.append("IFSC")
        checks.append(
            CheckResult(
                code="BANK_DETAILS",
                passed=bank_ok,
                severity="warning",
                message="Bank details match vendor master"
                if bank_ok
                else f"Bank mismatch: {', '.join(bank_notes)}",
            )
        )

    def failed(code: str) -> bool:
        return any(c.code == code and not c.passed for c in checks)

    if failed("DUPLICATE") or (vendor and failed("VENDOR_APPROVED")):
        outcome = Outcome.REJECT
    elif po and (failed("PO_OPEN") or failed("PO_VENDOR")):
        outcome = Outcome.REJECT
    elif any(
        failed(code)
        for code in (
            "REQUIRED_FIELDS",
            "VENDOR_GSTIN",
            "VENDOR_IDENTITY",
            "VENDOR_APPROVED",
            "PO_FOUND",
            "PO_AMOUNT",
            "PO_NET",
            "GST_MATH",
            "INVOICE_ARITHMETIC",
            "LINE_ITEMS",
        )
    ):
        outcome = Outcome.REVIEW
    else:
        outcome = Outcome.APPROVE
        if not reasons:
            reasons.append("All required checks passed")

    return Decision(
        source_file=invoice.source_file,
        outcome=outcome,
        invoice=invoice,
        vendor_id=vendor_id,
        po_number=po_number,
        checks=checks,
        reasons=reasons,
        line_matches=line_matches,
    )
