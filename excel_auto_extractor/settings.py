"""Environment settings (from .env). Business rules live in constants.py."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Workspace sits next to the code (not the terminal's current folder) so it is always easy to find.
_ws = Path(os.getenv("WORKSPACE_DIR", "workspace"))
WORKSPACE = (_ws if _ws.is_absolute() else BASE_DIR / _ws).resolve()
RUNS_DIR = WORKSPACE / "runs"        # one folder per processed .msg: downloads, JSON files, log
OUTPUTS_DIR = WORKSPACE / "outputs"  # the final Excel files
for _d in (RUNS_DIR, OUTPUTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Tried in order. If a model is unavailable or its free quota is used up, the next one is used automatically.
GEMINI_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_MODELS",
    os.getenv("GEMINI_MODEL", "gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-2.5-flash"),
).split(",") if m.strip()]
