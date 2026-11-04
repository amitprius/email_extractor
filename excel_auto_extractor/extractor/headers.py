"""Read the column names of every sheet of every downloaded Excel and save them as JSON."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import utils
from .logger import RunLogger


def detect_header_row(raw: pd.DataFrame, scan_rows: int = 25) -> int:
    """The header is the row (within the first 25) with the most text cells; titles above it are skipped."""
    best_idx, best_score = 0, -1.0
    for idx in range(min(scan_rows, len(raw))):
        cells = raw.iloc[idx].dropna()
        if cells.empty:
            continue
        texty = sum(isinstance(v, str) and v.strip() != "" and not v.strip().replace(".", "").isdigit() for v in cells)
        score = texty + 0.1 * len(cells)
        if score > best_score:
            best_idx, best_score = idx, score
    return best_idx


def read_sheet(path: str, sheet: str) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)


def index_sheet(raw: pd.DataFrame, n_samples: int) -> dict | None:
    if raw.dropna(how="all").empty:
        return None
    hr = detect_header_row(raw)
    body = raw.iloc[hr + 1:]
    columns, positions, samples, seen = [], [], {}, {}
    for pos, val in enumerate(raw.iloc[hr]):
        if pd.isna(val) or str(val).strip() == "":
            continue
        name = " ".join(utils.clean_text(str(val)).split())
        if not name:
            continue
        if name in seen:                 # duplicate header names get a numeric suffix
            seen[name] += 1
            name = f"{name}__{seen[name]}"
        else:
            seen[name] = 1
        columns.append(name)
        positions.append(pos)
        vals = body.iloc[:, pos].dropna() if pos < body.shape[1] else []
        samples[name] = [utils.clean_text(str(v))[:40] for v in list(vals)[:n_samples]]
    return {"header_row": int(hr), "columns": columns, "col_positions": positions, "samples": samples,
            "data_rows": int(body.dropna(how="all").shape[0])}


def build_header_index(files: list, out_json: Path, log: RunLogger, n_samples: int) -> list:
    """Returns a flat list of 'units' (one per usable sheet) and saves excel_headers.json."""
    units, saved = [], {}
    for fi, f in enumerate(files, 1):
        entry = {"filename": f["filename"], "email_trail": f["email_trail"], "size": f["size"], "sheets": {}}
        saved[f["file_key"]] = entry
        try:
            with pd.ExcelFile(f["path"]) as xl:
                sheet_names = list(xl.sheet_names)
            for sheet in sheet_names:
                info = index_sheet(read_sheet(f["path"], sheet), n_samples)
                if not info:
                    log.info(f"   {f['filename']} › '{sheet}': empty sheet, skipped")
                    continue
                entry["sheets"][sheet] = {k: info[k] for k in ("header_row", "columns", "data_rows")}
                units.append({"unit_id": f"f{fi}::{sheet}", "file": f, "sheet": sheet, **info})
                shown = ", ".join(info["columns"][:12]) + (" …" if len(info["columns"]) > 12 else "")
                log.info(f"   {f['filename']} › '{sheet}': {len(info['columns'])} columns, {info['data_rows']} rows → {shown}")
        except Exception as e:  # damaged / password-protected workbook: log it and carry on with the others
            entry["error"] = str(e)
            log.error(f"   cannot read '{f['filename']}': {type(e).__name__}: {e}")
    out_json.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    return units
