from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVOICES = ROOT / "input_dataset" / "invoices"
DEFAULT_DATA = ROOT / "input_dataset" / "data"
DEFAULT_OUTPUT = ROOT / "output"

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash")
AMOUNT_TOLERANCE = 0.05
QTY_TOLERANCE = 0.001
