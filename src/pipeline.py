from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.config import DEFAULT_DATA, DEFAULT_INVOICES, DEFAULT_OUTPUT
from src.extract.service import extract_invoice
from src.master_data import MasterData
from src.models import Decision
from src.validate import validate_invoice


def process_invoice(path: Path, master: MasterData, use_llm: bool = True) -> Decision:
    invoice = extract_invoice(path, use_llm=use_llm)
    decision = validate_invoice(invoice, master)
    master.record_processed(decision)
    return decision


def process_folder(
    invoices_dir: Path,
    data_dir: Path,
    use_llm: bool = True,
    master: MasterData | None = None,
) -> list[Decision]:
    master = master or MasterData(data_dir)
    paths = sorted(Path(invoices_dir).glob("*.pdf"))
    return [process_invoice(path, master, use_llm=use_llm) for path in paths]


def decisions_to_json(decisions: list[Decision]) -> list[dict]:
    return [d.model_dump(mode="json") for d in decisions]


def compare_expected(decisions: list[Decision], master: MasterData) -> list[dict]:
    if master.expected_outcomes.empty:
        return []
    expected = {
        row["file"]: row["expected_outcome"]
        for _, row in master.expected_outcomes.iterrows()
    }
    rows = []
    for decision in decisions:
        exp = expected.get(decision.source_file)
        rows.append(
            {
                "file": decision.source_file,
                "outcome": decision.outcome.value,
                "expected": exp,
                "match": exp == decision.outcome.value if exp else None,
                "vendor_id": decision.vendor_id,
                "po_number": decision.po_number,
                "reasons": decision.reasons,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Invoice processing prototype")
    parser.add_argument("--invoices", type=Path, default=DEFAULT_INVOICES)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument(
        "--agent",
        action="store_true",
        help="Route each invoice through the Gemini tool-calling agent",
    )
    args = parser.parse_args()

    master = MasterData(args.data)
    if args.agent:
        from src.agent import run_agent_on_folder

        decisions = run_agent_on_folder(args.invoices, master, use_llm=not args.no_llm)
    else:
        decisions = process_folder(
            args.invoices, args.data, use_llm=not args.no_llm, master=master
        )

    summary = compare_expected(decisions, master)
    args.output.mkdir(parents=True, exist_ok=True)
    out_path = args.output / "results.json"
    review_queue = [
        {
            "file": d.source_file,
            "outcome": d.outcome.value,
            "reasons": d.reasons,
            "vendor_id": d.vendor_id,
            "po_number": d.po_number,
        }
        for d in decisions
        if d.outcome.value != "APPROVE"
    ]
    out_path.write_text(
        json.dumps(
            {
                "summary": summary,
                "review_queue": review_queue,
                "decisions": decisions_to_json(decisions),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"{'file':<42} {'outcome':<10} {'expected':<10} {'ok'}")
    print("-" * 72)
    for row in summary or [
        {
            "file": d.source_file,
            "outcome": d.outcome.value,
            "expected": "",
            "match": True,
        }
        for d in decisions
    ]:
        flag = "Y" if row.get("match") else "N"
        print(f"{row['file']:<42} {row['outcome']:<10} {str(row.get('expected') or ''):<10} {flag}")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
