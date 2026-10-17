from __future__ import annotations

from pathlib import Path

import pandas as pd


class MasterData:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.vendors = pd.read_csv(self.data_dir / "vendor_master.csv", dtype=str)
        self.purchase_orders = pd.read_csv(self.data_dir / "purchase_orders.csv")
        self.po_line_items = pd.read_csv(self.data_dir / "po_line_items.csv")
        history_path = self.data_dir / "invoice_history.csv"
        self.invoice_history = pd.read_csv(history_path)
        expected_path = self.data_dir / "expected_outcomes.csv"
        self.expected_outcomes = (
            pd.read_csv(expected_path) if expected_path.exists() else pd.DataFrame()
        )
        for col in (
            "net_amount",
            "gst_rate_pct",
            "gst_amount",
            "total_amount",
            "billed_to_date",
            "remaining_balance",
        ):
            if col in self.purchase_orders.columns:
                self.purchase_orders[col] = pd.to_numeric(
                    self.purchase_orders[col], errors="coerce"
                )
        for col in ("qty", "unit_price", "line_net_amount"):
            if col in self.po_line_items.columns:
                self.po_line_items[col] = pd.to_numeric(
                    self.po_line_items[col], errors="coerce"
                )
        if "remaining_qty" not in self.po_line_items.columns:
            self.po_line_items["remaining_qty"] = self.po_line_items["qty"]
        else:
            self.po_line_items["remaining_qty"] = pd.to_numeric(
                self.po_line_items["remaining_qty"], errors="coerce"
            )
        if "remaining_net" not in self.purchase_orders.columns:
            billed = self.purchase_orders["billed_to_date"].fillna(0)
            gst = self.purchase_orders["gst_rate_pct"].fillna(0)
            denom = 1 + gst / 100.0
            self.purchase_orders["remaining_net"] = (
                self.purchase_orders["net_amount"] - billed / denom
            )
        self.vendors = self.vendors.fillna("")
        if not self.invoice_history.empty:
            self.invoice_history = self.invoice_history.fillna("")

    def vendor_by_gstin(self, gstin: str) -> dict | None:
        if not gstin:
            return None
        match = self.vendors[self.vendors["gstin"].str.upper() == gstin.upper()]
        if match.empty:
            return None
        return match.iloc[0].to_dict()

    def vendor_by_id(self, vendor_id: str) -> dict | None:
        match = self.vendors[self.vendors["vendor_id"] == vendor_id]
        if match.empty:
            return None
        return match.iloc[0].to_dict()

    def vendor_rows(self) -> list[dict]:
        return self.vendors.to_dict(orient="records")

    def po_by_number(self, po_number: str) -> dict | None:
        if not po_number:
            return None
        match = self.purchase_orders[
            self.purchase_orders["po_number"].astype(str) == po_number
        ]
        if match.empty:
            return None
        return match.iloc[0].to_dict()

    def po_lines(self, po_number: str) -> list[dict]:
        match = self.po_line_items[
            self.po_line_items["po_number"].astype(str) == po_number
        ]
        return match.to_dict(orient="records")

    def history_rows(self) -> list[dict]:
        if self.invoice_history.empty:
            return []
        return self.invoice_history.to_dict(orient="records")

    def record_processed(self, decision) -> None:
        """Keep in-memory history and PO remaining so later invoices in the same run see it."""
        from src.models import Outcome

        invoice = decision.invoice
        row = {
            "invoice_number": invoice.invoice_number or "",
            "vendor_id": decision.vendor_id or "",
            "po_number": decision.po_number or "",
            "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else "",
            "total_amount": invoice.total if invoice.total is not None else "",
            "status": decision.outcome.value,
            "paid_on": "",
            "note": "; ".join(decision.reasons),
        }
        self.invoice_history = pd.concat(
            [self.invoice_history, pd.DataFrame([row])],
            ignore_index=True,
        )
        if decision.outcome != Outcome.APPROVE or not decision.po_number:
            return
        po_mask = self.purchase_orders["po_number"].astype(str) == str(decision.po_number)
        if not po_mask.any():
            return
        total = float(invoice.total or 0)
        subtotal = float(invoice.subtotal or 0)
        self.purchase_orders.loc[po_mask, "billed_to_date"] = (
            self.purchase_orders.loc[po_mask, "billed_to_date"].fillna(0) + total
        )
        self.purchase_orders.loc[po_mask, "remaining_balance"] = (
            self.purchase_orders.loc[po_mask, "remaining_balance"] - total
        )
        self.purchase_orders.loc[po_mask, "remaining_net"] = (
            self.purchase_orders.loc[po_mask, "remaining_net"] - subtotal
        )
        for match in decision.line_matches:
            line_mask = self.po_line_items["po_number"].astype(str) == str(decision.po_number)
            if match.po_line_no is not None:
                line_mask = line_mask & (self.po_line_items["line_no"] == match.po_line_no)
            elif match.item_code:
                line_mask = line_mask & (self.po_line_items["item_code"] == match.item_code)
            if line_mask.any():
                self.po_line_items.loc[line_mask, "remaining_qty"] = (
                    self.po_line_items.loc[line_mask, "remaining_qty"] - match.qty
                )
