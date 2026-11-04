"""Streamlit UI:  streamlit run app.py

Upload a .msg  ->  it runs by itself (no questions asked)  ->  download the output Excel.
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import streamlit as st

import constants
import settings
from extractor.logger import STEPS, RunLogger
from extractor.pipeline import run_pipeline
from extractor.utils import human_size

st.set_page_config(page_title="Excel Column Extractor", page_icon="📊", layout="wide")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ICON = {"pending": "⚪", "running": "⏳", "done": "✅", "skipped": "⏭️", "warning": "⚠️", "failed": "❌"}


def show_df(df: pd.DataFrame) -> None:
    try:
        st.dataframe(df, hide_index=True, width="stretch")
    except Exception:  # older Streamlit versions
        st.dataframe(df, hide_index=True, use_container_width=True)


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("📊 Excel Column Extractor")
    st.caption("Input: one .msg file → Output: one Excel with the fixed columns below.")
    st.markdown("**Output columns** (edit `constants.py`)")
    st.code("\n".join(constants.TARGET_COLUMNS), language=None)
    st.markdown("**Matching order**")
    st.markdown("1. exact / alias name\n2. fuzzy name" + ("" if constants.USE_FUZZY else " (off)") +
                "\n3. Gemini, only for what is left" + ("" if constants.USE_AI else " (off)"))
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        api_key = st.text_input("Gemini API key", type="password", help="Or set GEMINI_API_KEY in the .env file").strip()
    st.caption(f"Gemini key: {'✅ set' if api_key else '❌ missing (AI step will be skipped)'}")
    st.caption("Models (in order): " + ", ".join(settings.GEMINI_MODELS))
    st.markdown("**Folders**")
    st.code(f"Output Excel files:\n{settings.OUTPUTS_DIR}\n\nRuns (downloads, JSON, logs):\n{settings.RUNS_DIR}", language=None)

st.header("Upload the .msg file")
up = st.file_uploader("The email (attachments and nested emails are opened automatically)", type=["msg", "eml"])


# ------------------------------------------------------------------ run
def process(raw: bytes, name: str, key: str) -> dict:
    st.subheader("Progress")
    bar = st.progress(0.0, text="Starting…")
    steps_ph = st.empty()
    st.caption("Live log")
    log_ph = st.empty()
    state = {k: ("pending", "") for k, _ in STEPS}

    def draw_steps() -> None:
        steps_ph.markdown("\n\n".join(
            f"{ICON[state[k][0]]} **{label}**" + (f" — {state[k][1]}" if state[k][1] else "") for k, label in STEPS))

    def on_line(line: str) -> None:
        log_ph.code("\n".join(log.lines[-22:]), language=None)   # newest lines; the full log is kept below afterwards

    def on_step(k: str, s: str, detail: str) -> None:
        state[k] = (s, detail)
        draw_steps()

    log = RunLogger(on_line=on_line, on_step=on_step,
                    on_progress=lambda f, t: bar.progress(f, text=t))
    draw_steps()
    report = run_pipeline(raw, name, log=log, api_key=key)
    bar.progress(1.0, text="Finished" if report["status"] == "success" else "Finished with problems")
    report["step_state"] = state
    return report


def show_result(rep: dict, fresh: bool) -> None:
    st.divider()
    t = rep["token_usage"]
    if rep["status"] == "success":
        st.success(f"✅ Output file generated: **{rep['output_file'].split('/')[-1].split(chr(92))[-1]}** — "
                   f"{rep['output_rows']} rows × {len(constants.TARGET_COLUMNS)} columns")
        if fresh:
            st.toast("Output file generated", icon="✅")
        path = rep["output_file"]
        with open(path, "rb") as fh:
            st.download_button("⬇ Download output Excel", fh.read(), file_name=path.replace("\\", "/").split("/")[-1],
                               mime=XLSX_MIME, type="primary")
        st.caption(f"Saved at: {path}")
    else:
        st.error("No output file was generated. See the log below for the reason.")

    c = st.columns(5)
    c[0].metric("Excel files used", len(rep["excel_files"]))
    c[1].metric("Output rows", rep["output_rows"])
    c[2].metric("Columns not found", len(rep["unmatched_targets"]))
    c[3].metric("AI calls", t["ai_calls"])
    c[4].metric("Total tokens", f"{t['total_tokens']:,}")
    if rep["unmatched_targets"]:
        st.warning("Not found in any file (left empty): " + ", ".join(rep["unmatched_targets"]))

    if rep["status"] == "success":
        with st.expander("Preview of the output (first 100 rows)"):
            show_df(pd.read_excel(rep["output_file"], sheet_name=constants.OUTPUT_SHEET_NAME, nrows=100))
    with st.expander("Column mapping used", expanded=True):
        if rep["mapping"]:
            show_df(pd.DataFrame(rep["mapping"]))
    with st.expander("Excel attachments found"):
        if rep["excel_files"]:
            df = pd.DataFrame(rep["excel_files"])
            df["found in"] = df["email_trail"].map(" › ".join)
            df["size"] = df["size"].map(human_size)
            show_df(df[["filename", "size", "depth", "found in", "file_key"]])
        for kind, label in (("duplicates", "Skipped duplicates"), ("ignored_attachments", "Ignored (not Excel / unreadable)")):
            if rep[kind]:
                st.markdown(f"**{label}**")
                show_df(pd.DataFrame(rep[kind]))
    with st.expander("Token usage (Gemini)"):
        st.write(f"Prompt **{t['prompt_tokens']:,}** · Output **{t['output_tokens']:,}** · "
                 f"Thinking **{t['thinking_tokens']:,}** · Total **{t['total_tokens']:,}** · Calls **{t['ai_calls']}**")
        if rep["token_calls"]:
            show_df(pd.DataFrame(rep["token_calls"]))
    with st.expander("Full log" if fresh else "Full log (this run)", expanded=not fresh):
        st.code("\n".join(rep["log_lines"]), language=None)
    cols = st.columns(3)
    cols[0].download_button("⬇ run.log", "\n".join(rep["log_lines"]).encode(), "run.log", "text/plain")
    cols[1].download_button("⬇ run_report.json", json.dumps({k: v for k, v in rep.items() if k != "log_lines"},
                                                            indent=2, default=str).encode(), "run_report.json", "application/json")
    import os
    hp = os.path.join(rep["run_dir"], "excel_headers.json")
    if os.path.exists(hp):
        cols[2].download_button("⬇ excel_headers.json", open(hp, "rb").read(), "excel_headers.json", "application/json")


if up is not None:
    raw = up.getvalue()
    sig = (up.name, hashlib.sha1(raw).hexdigest())
    again = st.button("↻ Process this file again")
    if st.session_state.get("sig") != sig or again:
        st.session_state["sig"] = sig
        rep = process(raw, up.name, api_key)
        st.session_state["report"] = rep
        show_result(rep, fresh=True)
    elif "report" in st.session_state:
        show_result(st.session_state["report"], fresh=False)
else:
    st.info("Upload a .msg file to start. Everything after that runs automatically.")

# ------------------------------------------------------------------ history
runs = []
for p in sorted(settings.RUNS_DIR.glob("*/run_report.json"), reverse=True)[:50]:
    try:
        r = json.loads(p.read_text(encoding="utf-8"))
        runs.append({"run": r["run_id"], "input": r["input_file"], "status": r["status"],
                     "output file": (r.get("output_file") or "").replace("\\", "/").split("/")[-1],
                     "rows": r.get("output_rows", 0), "size": human_size(r.get("output_size", 0)),
                     "tokens": r.get("token_usage", {}).get("total_tokens", 0), "finished": r.get("finished_at", "")})
    except Exception:
        continue
if runs:
    with st.expander(f"Previous runs ({len(runs)})"):
        show_df(pd.DataFrame(runs))
