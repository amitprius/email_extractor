"""Run logger (file + live UI callback) and Gemini token accounting."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

STEPS = [
    ("extract", "Read email & download Excel attachments"),
    ("headers", "Save column names of every Excel"),
    ("match", "Match columns (exact / fuzzy)"),
    ("ai", "AI matching for remaining columns"),
    ("build", "Build output Excel"),
]


class TokenUsage:
    """Adds up the token counts Gemini reports for every call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def add(self, model: str, usage, sheets: int, seconds: float) -> dict:
        g = lambda name: int(getattr(usage, name, 0) or 0)  # noqa: E731  (usage fields may be missing/None)
        rec = {"call": len(self.calls) + 1, "model": model, "sheets": sheets, "seconds": round(seconds, 1),
               "prompt_tokens": g("prompt_token_count"), "output_tokens": g("candidates_token_count"),
               "thinking_tokens": g("thoughts_token_count"), "total_tokens": g("total_token_count")}
        if not rec["total_tokens"]:
            rec["total_tokens"] = rec["prompt_tokens"] + rec["output_tokens"] + rec["thinking_tokens"]
        self.calls.append(rec)
        return rec

    @property
    def totals(self) -> dict:
        keys = ("prompt_tokens", "output_tokens", "thinking_tokens", "total_tokens")
        return {"ai_calls": len(self.calls), **{k: sum(c[k] for c in self.calls) for k in keys}}


class RunLogger:
    """Every message goes to the run's log file AND to the UI (through the callbacks)."""

    def __init__(self, log_path: Optional[Path] = None,
                 on_line: Optional[Callable[[str], None]] = None,
                 on_step: Optional[Callable[[str, str, str], None]] = None,
                 on_progress: Optional[Callable[[float, str], None]] = None) -> None:
        self.lines: list[str] = []
        self.log_path = log_path
        self.on_line, self.on_step, self.on_progress = on_line, on_step, on_progress
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("", encoding="utf-8")

    def attach_file(self, log_path: Path) -> None:
        """Start writing to this file (lines logged so far are written first)."""
        self.log_path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("".join(line + "\n" for line in self.lines), encoding="utf-8")

    def _emit(self, level: str, msg: str) -> None:
        line = f"{datetime.now():%H:%M:%S} | {level:<7} | {msg}"
        self.lines.append(line)
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        if self.on_line:
            self.on_line(line)

    def info(self, msg: str) -> None: self._emit("INFO", msg)
    def warn(self, msg: str) -> None: self._emit("WARNING", msg)
    def error(self, msg: str) -> None: self._emit("ERROR", msg)
    def ok(self, msg: str) -> None: self._emit("SUCCESS", msg)

    def step(self, key: str, state: str, detail: str = "") -> None:
        """state: running | done | skipped | warning | failed"""
        if self.on_step:
            self.on_step(key, state, detail)

    def progress(self, fraction: float, text: str = "") -> None:
        if self.on_progress:
            self.on_progress(max(0.0, min(1.0, fraction)), text)
