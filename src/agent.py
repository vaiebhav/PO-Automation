from __future__ import annotations

import json
from pathlib import Path

from src.config import GEMINI_MODEL, GOOGLE_API_KEY
from src.extract.service import extract_invoice
from src.master_data import MasterData
from src.models import Decision, Invoice
from src.validate import validate_invoice


TOOL_DECLARATIONS = [
    {
        "name": "extract_invoice",
        "description": "Extract structured fields from a local invoice PDF path.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the PDF"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "lookup_vendor",
        "description": "Look up a vendor by GSTIN or vendor name.",
        "parameters": {
            "type": "object",
            "properties": {
                "gstin": {"type": "string"},
                "vendor_name": {"type": "string"},
            },
        },
    },
    {
        "name": "lookup_po",
        "description": "Look up a purchase order and its line items by PO number.",
        "parameters": {
            "type": "object",
            "properties": {
                "po_number": {"type": "string"},
            },
            "required": ["po_number"],
        },
    },
    {
        "name": "validate_invoice",
        "description": "Run deterministic matching rules and return APPROVE/REVIEW/REJECT.",
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_json": {
                    "type": "string",
                    "description": "JSON for the extracted invoice",
                }
            },
            "required": ["invoice_json"],
        },
    },
]


class InvoiceTools:
    def __init__(self, master: MasterData, use_llm: bool = True) -> None:
        self.master = master
        self.use_llm = use_llm
        self.last_decision: Decision | None = None

    def extract_invoice(self, path: str) -> dict:
        invoice = extract_invoice(Path(path), use_llm=self.use_llm)
        return invoice.model_dump(mode="json")

    def lookup_vendor(self, gstin: str | None = None, vendor_name: str | None = None) -> dict:
        if gstin:
            row = self.master.vendor_by_gstin(gstin)
            if row:
                return row
        if vendor_name:
            from rapidfuzz import fuzz

            best, score = None, 0
            for row in self.master.vendor_rows():
                s = fuzz.token_set_ratio(vendor_name, row["vendor_name"])
                if s > score:
                    best, score = row, s
            if best and score >= 85:
                return {**best, "match_score": score}
        return {"found": False}

    def lookup_po(self, po_number: str) -> dict:
        po = self.master.po_by_number(po_number)
        if not po:
            return {"found": False, "po_number": po_number}
        return {
            "found": True,
            "purchase_order": po,
            "line_items": self.master.po_lines(po_number),
        }

    def validate_invoice(self, invoice_json: str | dict) -> dict:
        payload = json.loads(invoice_json) if isinstance(invoice_json, str) else invoice_json
        invoice = Invoice.model_validate(payload)
        decision = validate_invoice(invoice, self.master)
        self.last_decision = decision
        return decision.model_dump(mode="json")

    def dispatch(self, name: str, args: dict) -> dict:
        if name == "extract_invoice":
            return self.extract_invoice(args["path"])
        if name == "lookup_vendor":
            return self.lookup_vendor(args.get("gstin"), args.get("vendor_name"))
        if name == "lookup_po":
            return self.lookup_po(args["po_number"])
        if name == "validate_invoice":
            return self.validate_invoice(args["invoice_json"])
        return {"error": f"Unknown tool {name}"}


def run_agent_on_invoice(path: Path, master: MasterData, use_llm: bool = True) -> Decision:
    """Gemini tool loop; falls back to the local pipeline if no API key."""
    tools = InvoiceTools(master, use_llm=use_llm)
    if not GOOGLE_API_KEY:
        invoice = extract_invoice(path, use_llm=False)
        decision = validate_invoice(invoice, master)
        master.record_processed(decision)
        return decision

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GOOGLE_API_KEY)
    declarations = [
        types.FunctionDeclaration(
            name=item["name"],
            description=item["description"],
            parameters=item["parameters"],
        )
        for item in TOOL_DECLARATIONS
    ]
    config = types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=declarations)],
    )
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part(
                    text=(
                        "Process this vendor invoice end to end. "
                        "Call extract_invoice, then lookup_vendor and lookup_po using extracted fields, "
                        "then validate_invoice with the extracted invoice JSON. "
                        f"PDF path: {path}"
                    )
                )
            ],
        )
    ]

    for _ in range(8):
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=config,
        )
        candidate = response.candidates[0]
        parts = candidate.content.parts or []
        calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
        if not calls:
            break
        contents.append(candidate.content)
        fn_parts = []
        for call in calls:
            args = dict(call.args or {})
            result = tools.dispatch(call.name, args)
            fn_parts.append(
                types.Part.from_function_response(name=call.name, response=result)
            )
        contents.append(types.Content(role="tool", parts=fn_parts))
        if tools.last_decision is not None:
            master.record_processed(tools.last_decision)
            return tools.last_decision

    if tools.last_decision is not None:
        master.record_processed(tools.last_decision)
        return tools.last_decision
    decision = validate_invoice(extract_invoice(path, use_llm=use_llm), master)
    master.record_processed(decision)
    return decision


def run_agent_on_folder(
    invoices_dir: Path, master: MasterData, use_llm: bool = True
) -> list[Decision]:
    paths = sorted(Path(invoices_dir).glob("*.pdf"))
    return [run_agent_on_invoice(path, master, use_llm=use_llm) for path in paths]
