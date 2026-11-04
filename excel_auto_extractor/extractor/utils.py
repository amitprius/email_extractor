from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")      # characters Excel cannot store
INVISIBLE = re.compile(r"[\x00-\x1f\x7f​-‏ - ﻿]")
EXCEL_EXT = {".xlsx", ".xlsm", ".xls"}
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")               # .msg and old .xls files


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def human_size(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def clean_text(v):
    """Strip control characters (.msg fields often end with NUL characters)."""
    return INVISIBLE.sub("", v).strip() if isinstance(v, str) else v


def safe_name(name: str, default: str = "file") -> str:
    """File name safe to write: no folders, no control characters (they are removed, not replaced)."""
    name = INVISIBLE.sub("", str(name).replace("\\", "/"))
    name = Path(name).name.strip().strip(".")
    name = re.sub(r"[^\w.\- ()]", "_", name)
    name = re.sub(r"(\.[A-Za-z0-9]{2,5})_+$", r"\1", name)   # 'Report.xlsx_' -> 'Report.xlsx'
    return name[:150] or default


def slug(text: str, maxlen: int = 30) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-").lower()[:maxlen] or "email"


def sniff_excel_ext(data: bytes, name: str = "") -> str | None:
    """Recognise an Excel file by its CONTENT, whatever its (possibly damaged or missing) name says."""
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "xl/workbook.xml" in z.namelist():
                    return ".xlsm" if ".xlsm" in name.lower() else ".xlsx"
        except zipfile.BadZipFile:
            return None
    if data[:8] == OLE_MAGIC and ".xls" in name.lower():
        return ".xls"
    return None
