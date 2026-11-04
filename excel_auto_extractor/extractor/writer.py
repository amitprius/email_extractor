"""Build the output Excel: only the TARGET_COLUMNS, stacked from every sheet that matched."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import constants

from .headers import read_sheet
from .logger import RunLogger
from .utils import clean_text

SHEET_ROWS = 1_048_575   # rows that fit on one Excel sheet; more rows continue on "<name>_2", "<name>_3" ...


def _clean_value(v):
    v = clean_text(v)
    return None if isinstance(v, str) and v == "" else v


def build_output(units: list[dict], assigned: dict, out_path: Path, log: RunLogger) -> dict:
    targets = constants.TARGET_COLUMNS
    frames, mapping_rows = [], []
    for u in units:
        a = assigned.get(u["unit_id"], {})
        if len(a) < constants.MIN_MATCHED_TARGETS_PER_SHEET:
            continue
        raw = read_sheet(u["file"]["path"], u["sheet"])
        body = raw.iloc[u["header_row"] + 1:]
        pos_of = dict(zip(u["columns"], u["col_positions"]))
        df = pd.DataFrame({t: (body.iloc[:, pos_of[a[t]["source"]]].values if t in a else None) for t in targets})
        df = df.apply(lambda col: col.map(_clean_value)).dropna(how="all").reset_index(drop=True)
        label = f"{u['file']['filename']} › '{u['sheet']}'"
        log.info(f"   {label}: {len(df)} row(s) from {len(a)}/{len(targets)} column(s)")
        if df.empty:
            continue
        frames.append(df)
        for t in targets:
            mapping_rows.append({"output_column": t, "source_file": u["file"]["filename"], "source_sheet": u["sheet"],
                                 "source_column": a[t]["source"] if t in a else "", "method": a[t]["method"] if t in a else "not found",
                                 "score": a[t]["score"] if t in a else "", "rows": len(df)})
    if not frames:
        return {"rows": 0}

    result = pd.concat(frames, ignore_index=True)[targets]
    with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
        name = constants.OUTPUT_SHEET_NAME
        for i, start in enumerate(range(0, max(len(result), 1), SHEET_ROWS)):
            result.iloc[start:start + SHEET_ROWS].to_excel(xw, sheet_name=name if i == 0 else f"{name}_{i + 1}", index=False)
        if constants.INCLUDE_MAPPING_SHEET:
            pd.DataFrame(mapping_rows).to_excel(xw, sheet_name="Mapping", index=False)
    fix = bool(result.apply(lambda c: c.map(lambda v: isinstance(v, str) and v.startswith("="))).any().any())
    _style(out_path, fix)
    return {"rows": int(len(result)), "mapping_rows": mapping_rows}


def _style(path: Path, fix_formula_text: bool) -> None:
    wb = load_workbook(path)
    for ws in wb.worksheets:
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.freeze_panes = "A2"
        for i, col in enumerate(ws.columns, 1):
            width = max((len(str(c.value)) for c in col[:200] if c.value is not None), default=8)
            ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 12), 50)
        if fix_formula_text:                          # text that starts with '=' must stay text, never become a formula
            for row in ws.iter_rows(min_row=2):
                for c in row:
                    if isinstance(c.value, str) and c.value.startswith("="):
                        c.data_type = "s"
    wb.save(path)
