"""The whole job, start to finish, with no human in the loop:

   .msg  ->  Excel attachments (nested emails included)  ->  column names saved (JSON)
         ->  exact / alias / fuzzy matching  ->  Gemini for the rest  ->  ONE output Excel

Never raises for data problems: everything is logged and the result says what happened.
"""
from __future__ import annotations

import json
import shutil
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import constants
import settings

from . import ai_mapper
from .headers import build_header_index
from .logger import RunLogger, TokenUsage
from .matcher import Matcher
from .msg_reader import extract_excels
from .utils import human_size, now_iso, safe_name
from .writer import build_output


def run_pipeline(raw: bytes, filename: str, log: Optional[RunLogger] = None, api_key: Optional[str] = None,
                 client: Any = None) -> dict:
    t0 = time.time()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = settings.RUNS_DIR / run_id
    n = 1
    while run_dir.exists():                      # two runs in the same second
        n += 1
        run_dir = settings.RUNS_DIR / f"{run_id}_{n}"
    run_id = run_dir.name
    run_dir.mkdir(parents=True)
    log = log or RunLogger()
    log.attach_file(run_dir / "run.log")

    tokens = TokenUsage()
    key = api_key or settings.GEMINI_API_KEY
    report: dict = {"run_id": run_id, "input_file": filename, "status": "failed", "started_at": now_iso(),
                    "run_dir": str(run_dir), "log_file": str(run_dir / "run.log"), "output_file": None, "output_rows": 0,
                    "excel_files": [], "ignored_attachments": [], "duplicates": [], "mapping": [], "unmatched_targets": [],
                    "token_usage": tokens.totals, "token_calls": []}
    try:
        (run_dir / "input").mkdir()
        (run_dir / "input" / safe_name(filename, "input.msg")).write_bytes(raw)
        log.info(f"▶ RUN {run_id} started — input: {filename} ({human_size(len(raw))})")
        log.info(f"   target columns: {', '.join(constants.TARGET_COLUMNS)}")

        # ---- 1. read the email, save only Excel attachments
        log.step("extract", "running"); log.progress(0.05, "Reading email")
        log.info("STEP 1/5 — reading email and downloading Excel attachments (nested emails included)")
        ex = extract_excels(raw, filename, run_dir / "downloads", log)
        report.update(excel_files=[{k: v for k, v in f.items() if k != "path"} for f in ex.files],
                      ignored_attachments=ex.ignored, duplicates=ex.duplicates)
        (run_dir / "metadata.json").write_text(json.dumps(
            {"input_file": filename, "emails": ex.nodes, "excel_files": report["excel_files"],
             "ignored_attachments": ex.ignored, "duplicates": ex.duplicates}, indent=2, ensure_ascii=False), encoding="utf-8")
        depth = max((nd["depth"] for nd in ex.nodes.values()), default=0)
        log.info(f"   emails opened: {len(ex.nodes)} (deepest level {depth}) | Excel saved: {len(ex.files)} | "
                 f"duplicates skipped: {len(ex.duplicates)} | other attachments ignored: {len(ex.ignored)}")
        if not ex.files:
            log.step("extract", "failed", "no Excel attachment found")
            log.error("No Excel attachment found anywhere in this email (checked all nested emails). Nothing to process.")
            return _finish(report, log, tokens, t0)
        log.step("extract", "done", f"{len(ex.files)} Excel file(s)")

        # ---- 2. save column names
        log.step("headers", "running"); log.progress(0.25, "Reading column names")
        log.info("STEP 2/5 — reading and saving the column names of every Excel file")
        units = build_header_index(ex.files, run_dir / "excel_headers.json", log, constants.AI_SAMPLE_VALUES)
        log.info(f"   column names saved → {run_dir / 'excel_headers.json'}")
        if not units:
            log.step("headers", "failed", "no readable sheets")
            log.error("None of the Excel files has a readable sheet. Nothing to process.")
            return _finish(report, log, tokens, t0)
        log.step("headers", "done", f"{len(units)} sheet(s)")

        # ---- 3. exact / alias / fuzzy
        log.step("match", "running"); log.progress(0.40, "Matching column names")
        log.info(f"STEP 3/5 — matching column names ({'exact + alias + fuzzy' if constants.USE_FUZZY else 'exact + alias'}"
                 f"{', threshold ' + str(constants.FUZZY_THRESHOLD) if constants.USE_FUZZY else ''})")
        matcher = Matcher(constants.TARGET_COLUMNS, constants.ALIASES, constants.USE_FUZZY, constants.FUZZY_THRESHOLD)
        assigned, unresolved = {}, {}
        for u in units:
            a, miss = matcher.match_sheet(u["columns"])
            assigned[u["unit_id"]], unresolved[u["unit_id"]] = a, miss
            label = f"{u['file']['filename']} › '{u['sheet']}'"
            for t, m in a.items():
                log.info(f"   {label}: {t} ← '{m['source']}' [{m['method']} {m['score']:.2f}]")
            if miss:
                log.info(f"   {label}: still unmatched → {', '.join(miss)}")
        log.step("match", "done")

        # ---- 4. AI for what is left
        need = [u for u in units if unresolved[u["unit_id"]] and len(assigned[u["unit_id"]]) < len(u["columns"])]
        if not constants.USE_AI:
            log.info("STEP 4/5 — AI matching switched off in constants.py"); log.step("ai", "skipped", "switched off")
        elif not need:
            log.info("STEP 4/5 — AI matching not needed: every sheet is fully matched"); log.step("ai", "skipped", "not needed")
        elif not key and client is None:
            log.warn("STEP 4/5 — AI matching skipped: GEMINI_API_KEY is not set"); log.step("ai", "warning", "no API key")
        else:
            log.step("ai", "running"); log.progress(0.55, "Asking Gemini")
            log.info(f"STEP 4/5 — asking Gemini (fixed prompt) about {len(need)} sheet(s) with unmatched columns; "
                     f"models: {', '.join(settings.GEMINI_MODELS)}")
            found = ai_mapper.resolve(need, unresolved, assigned, key or "", settings.GEMINI_MODELS, tokens, log, client)
            for uid, picks in found.items():
                u = next(x for x in units if x["unit_id"] == uid)
                for t, m in picks.items():
                    assigned[uid][t] = m
                    log.info(f"   {u['file']['filename']} › '{u['sheet']}': {t} ← '{m['source']}' [ai {m['score']:.2f}] {m['reason']}")
            log.step("ai", "done" if tokens.calls else "warning", f"{len(tokens.calls)} call(s)")

        # ---- 5. output
        log.step("build", "running"); log.progress(0.80, "Building output Excel")
        log.info("STEP 5/5 — building the output Excel")
        out_dir = settings.OUTPUTS_DIR
        out_path = out_dir / f"{safe_name(Path(filename).stem, 'email')}_output_{run_id}.xlsx"
        res = build_output(units, assigned, out_path, log)
        used = {t for a in assigned.values() for t in a}
        report["unmatched_targets"] = [t for t in constants.TARGET_COLUMNS if t not in used]
        report["mapping"] = res.get("mapping_rows", [])
        if not res["rows"]:
            log.step("build", "failed", "no data")
            log.error("No output generated: no sheet had any of the target columns with data.")
            return _finish(report, log, tokens, t0)
        shutil.copy2(out_path, run_dir / out_path.name)
        report.update(status="success", output_file=str(out_path), output_rows=res["rows"],
                      output_size=out_path.stat().st_size)
        if report["unmatched_targets"]:
            log.warn(f"   target column(s) not found in any file (left empty): {', '.join(report['unmatched_targets'])}")
        log.step("build", "done", f"{res['rows']} rows")
        log.progress(1.0, "Done")
        log.ok(f"✅ OUTPUT FILE GENERATED: {out_path.name} — {res['rows']} row(s) × {len(constants.TARGET_COLUMNS)} columns "
               f"({human_size(report['output_size'])}) → {out_path}")
    except Exception as e:  # last resort: never crash the UI; the log explains
        log.error(f"Unexpected failure: {type(e).__name__}: {e}")
        log.error(traceback.format_exc(limit=4).replace("\n", " | "))
    return _finish(report, log, tokens, t0)


def _finish(report: dict, log: RunLogger, tokens: TokenUsage, t0: float) -> dict:
    t = tokens.totals
    report.update(token_usage=t, token_calls=tokens.calls, finished_at=now_iso(), duration_s=round(time.time() - t0, 1))
    log.info(f"TOTAL TOKEN USAGE: prompt={t['prompt_tokens']} | output={t['output_tokens']} | thinking={t['thinking_tokens']} "
             f"| TOTAL={t['total_tokens']} | AI calls={t['ai_calls']}")
    (log.log_path.parent / "run_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    log.info(f"■ RUN {report['run_id']} finished: {report['status'].upper()} in {report['duration_s']}s")
    report["log_lines"] = list(log.lines)
    return report
