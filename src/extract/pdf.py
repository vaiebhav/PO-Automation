from __future__ import annotations

from pathlib import Path

import pdfplumber


def extract_pdf(path: Path) -> dict:
    """Return raw text and tables from a PDF."""
    path = Path(path)
    texts: list[str] = []
    tables: list[list[list[str | None]]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
            for table in page.extract_tables() or []:
                tables.append(table)
    return {
        "path": str(path),
        "text": "\n".join(texts).strip(),
        "tables": tables,
    }
