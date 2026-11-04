"""Run:  python tests/test_pipeline.py        (needs no Gemini key, no network)"""
import io
import json
import os
import sys
import tempfile
import types
from email.message import EmailMessage
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ["WORKSPACE_DIR"] = TMP
os.environ["GEMINI_API_KEY"] = ""
os.environ["TZ"] = "UTC"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

import constants  # noqa: E402
import settings  # noqa: E402
from extractor import ai_mapper  # noqa: E402
from extractor.logger import RunLogger  # noqa: E402
from extractor.matcher import Matcher  # noqa: E402
from extractor.pipeline import run_pipeline  # noqa: E402

ai_mapper.time.sleep = lambda s: None          # no real waiting in retry tests
XL = ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
T = constants.TARGET_COLUMNS


def xlsx(df: pd.DataFrame, title_rows: int = 0) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, startrow=title_rows)
        for row in xw.sheets["Sheet1"].iter_rows():          # keep '=...' text as TEXT in the input file
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    c.data_type = "s"
    return buf.getvalue()


def mail(subject: str) -> EmailMessage:
    m = EmailMessage(); m["Subject"] = subject; m["From"] = "a@x.com"; m.set_content("body"); return m


# file A: exact + alias + fuzzy names only  -> needs NO AI
A = pd.DataFrame({"DU code": ["D1", "D2"], "DU name": ["Dove 1", "Dove 2"], "Lang cluster": ["EN", "FR"],
                  "Country Variant Name": ["UK", "FR"], "Action": ["add", "remove"], "CU code": ["C1", "C2"],
                  "Predecessor DU_Code": ["P1", None], "Successor DU_Code": [None, "S2"]})
# file B: title rows above header; 'Market' and 'Change Type' can only be matched by the AI
B = pd.DataFrame({"du_code": ["D3", "=HYPERLINK(\"http://x\")"], "Market": ["DE", "IT"], "Change Type": ["move", "  add  "],
                  "Unrelated": [1, 2]})
JUNK = pd.DataFrame({"Notes": ["nothing useful"], "Owner": ["bob"]})


def chain(with_ai_file: bool = True, with_excel: bool = True, with_junk: bool = True) -> bytes:
    l3 = mail("Layer 3 - final")
    if with_ai_file:
        l3.add_attachment(xlsx(B, title_rows=3), maintype=XL[0], subtype=XL[1], filename="needs_ai.xlsx_")  # wrong name
    l2 = mail("Layer 2 - forward"); l2.add_attachment(l3)
    if with_excel:
        l2.add_attachment(xlsx(A), maintype=XL[0], subtype=XL[1], filename="SCL_DU data.xlsx")   # duplicate of layer 1 file
    l1 = mail("Layer 1 - original")
    if with_excel:
        l1.add_attachment(xlsx(A), maintype=XL[0], subtype=XL[1], filename="SCL_DU data.xlsx")
        if with_junk:
            l1.add_attachment(xlsx(JUNK), maintype=XL[0], subtype=XL[1], filename="junk.xlsx")
    l1.add_attachment(b"\x89PNG....", maintype="image", subtype="png", filename="logo.png")
    l1.add_attachment(b"%PDF-1.4 ...", maintype="application", subtype="pdf", filename="spec.pdf")
    l1.add_attachment(l2)
    return l1.as_bytes()


class StubClient:
    """Pretends to be Gemini: answers from a tiny synonym table and reports token usage."""
    def __init__(self, fail: bool = False):
        self.calls, self.fail = 0, fail
        self.models = types.SimpleNamespace(generate_content=self._gen)

    def _gen(self, model, contents, config):
        self.calls += 1
        if self.fail:
            raise RuntimeError("503 service unavailable")
        req = json.loads(contents)
        syn = {"country": "Market", "action": "Change Type", "du_name": "Nonexistent column"}   # last one is a hallucination
        sheets = []
        for s in req["sheets"]:
            picks = [{"target": t, "source": syn.get(t), "confidence": 0.9, "reason": "test"} for t in s["unresolved_targets"]]
            sheets.append({"sheet_id": s["sheet_id"], "picks": picks})
        usage = types.SimpleNamespace(prompt_token_count=1000, candidates_token_count=100, thoughts_token_count=50, total_token_count=1150)
        return types.SimpleNamespace(parsed=None, text=json.dumps({"sheets": sheets}), usage_metadata=usage)


def run(raw, name="Assessment.msg", **kw):
    return run_pipeline(raw, name, log=RunLogger(), **kw)


# ---------------------------------------------------------------- 1. full run with AI
stub = StubClient()
rep = run(chain(), client=stub)
log = "\n".join(rep["log_lines"])
assert rep["status"] == "success", log
out = pd.read_excel(rep["output_file"], sheet_name="Data")
assert list(out.columns) == T, list(out.columns)
assert len(out) == 4, out                                     # 2 rows file A (once!) + 2 rows file B
assert rep["output_file"].endswith(".xlsx") and Path(rep["output_file"]).exists()
print("1 output      OK  columns", list(out.columns), "| rows", len(out))

assert len(rep["duplicates"]) == 1 and len(rep["excel_files"]) == 3, (rep["duplicates"], rep["excel_files"])
assert {i["filename"] for i in rep["ignored_attachments"]} == {"logo.png", "spec.pdf"}
assert any(f["filename"] == "needs_ai.xlsx" for f in rep["excel_files"]), "wrong-name Excel must be saved as .xlsx"
print("2 extraction  OK  3 Excel files, 1 duplicate skipped, ignored:", sorted(i["filename"] for i in rep["ignored_attachments"]))

methods = {(m["source_file"], m["output_column"]): m["method"] for m in rep["mapping"]}
assert methods[("SCL_DU data.xlsx", "du_code")] == "exact"
assert methods[("SCL_DU data.xlsx", "country")] == "alias"
assert methods[("SCL_DU data.xlsx", "language_cluster")] == "fuzzy"
assert methods[("needs_ai.xlsx", "country")] == "ai" and methods[("needs_ai.xlsx", "action")] == "ai"
assert methods[("needs_ai.xlsx", "du_name")] == "not found"   # the AI's invented column was rejected
assert "rejected" in log and "Nonexistent column" in log
print("3 matching    OK ", sorted(set(methods.values())))

assert stub.calls == 1                                         # one batched call for everything unresolved
assert rep["token_usage"] == {"ai_calls": 1, "prompt_tokens": 1000, "output_tokens": 100, "thinking_tokens": 50, "total_tokens": 1150}
assert "TOTAL TOKEN USAGE" in log and "TOTAL=1150" in log and "OUTPUT FILE GENERATED" in log
print("4 tokens+log  OK ", rep["token_usage"])

assert out.loc[out.du_code == "D3", "country"].iloc[0] == "DE"
assert set(out["action"].dropna()) >= {"move", "add"}                 # surrounding spaces trimmed
from extractor.writer import _clean_value  # noqa: E402
assert _clean_value("ab\x0bc\x00 ") == "abc" and _clean_value("  ") is None   # control characters never reach Excel
wb = __import__("openpyxl").load_workbook(rep["output_file"])
assert wb.sheetnames == ["Data", "Mapping"]
cell = [c for row in wb["Data"].iter_rows(min_row=2) for c in row if isinstance(c.value, str) and c.value.startswith("=")][0]
assert cell.data_type == "s", cell.data_type                         # '=HYPERLINK(...)' stays text, not a formula
hdr = json.loads((Path(rep["run_dir"]) / "excel_headers.json").read_text())
assert len(hdr) == 3 and all("sheets" in v for v in hdr.values())
assert (Path(rep["run_dir"]) / "metadata.json").exists() and (Path(rep["run_dir"]) / "run.log").exists()
assert (Path(rep["run_dir"]) / "run_report.json").exists()
print("5 data/files  OK  formula-safe, headers JSON + metadata + log saved")

# ---------------------------------------------------------------- 2. no AI needed -> zero tokens, client never called
stub2 = StubClient()
rep2 = run(chain(with_ai_file=False, with_junk=False), client=stub2)
assert rep2["status"] == "success" and stub2.calls == 0 and rep2["token_usage"]["total_tokens"] == 0
assert "AI matching not needed" in "\n".join(rep2["log_lines"])
print("6 no-AI path  OK  AI calls =", stub2.calls)

# ---------------------------------------------------------------- 3. AI down -> run still produces output
stub3 = StubClient(fail=True)
settings.GEMINI_MODELS[:] = ["m1", "m2"]
rep3 = run(chain(), client=stub3)
assert rep3["status"] == "success" and stub3.calls == 2 * constants.AI_RETRIES, stub3.calls
assert "continuing without it" in "\n".join(rep3["log_lines"])
o3 = pd.read_excel(rep3["output_file"], sheet_name="Data")
assert o3.loc[o3.du_code == "D3", "country"].isna().all()               # AI columns empty, deterministic ones still filled
print("7 AI failure  OK  tried", stub3.calls, "times over 2 models, output still produced")

# ---------------------------------------------------------------- 4. no key, no client
rep4 = run(chain())
assert rep4["status"] == "success" and "GEMINI_API_KEY is not set" in "\n".join(rep4["log_lines"])
print("8 no API key  OK ")

# ---------------------------------------------------------------- 5. no Excel anywhere
rep5 = run(chain(with_ai_file=False, with_excel=False))
assert rep5["status"] == "failed" and rep5["output_file"] is None and "No Excel attachment found" in "\n".join(rep5["log_lines"])
assert "TOTAL TOKEN USAGE" in "\n".join(rep5["log_lines"])
print("9 no Excel    OK  status =", rep5["status"])

# ---------------------------------------------------------------- 6. matcher behaviour
mt = Matcher(T, constants.ALIASES, True, 0.8)
a, miss = mt.match_sheet(["DU code", "Predecessor DU_Code", "Successor DU_Code", "CU code", "Language Cluster"])
assert a["du_code"]["source"] == "DU code" and a["predecessor_du"]["source"] == "Predecessor DU_Code"
assert a["successor_du"]["source"] == "Successor DU_Code" and a["cuc_code"]["source"] == "CU code"
a, miss = mt.match_sheet(["Predecessor DU_Code", "Successor DU_Code"])        # no plain 'DU code': must NOT steal one
assert "du_code" in miss and a["predecessor_du"]["source"] == "Predecessor DU_Code", (a, miss)
a, _ = mt.match_sheet(["duCode", "DUName", "Country", "ACTION "])
assert a["du_code"]["method"] == "exact" and a["du_name"]["method"] == "exact" and a["action"]["method"] == "exact"
print("10 matcher    OK ")

# ---------------------------------------------------------------- 7. real .msg files (if the samples were downloaded)
samples = os.environ.get("MSG_SAMPLES")
if samples and Path(samples).exists():
    for f in ["signed.msg", "unicode.msg", "multi-to.msg"]:
        r = run(Path(samples, f).read_bytes(), f)
        assert r["status"] == "failed" and "No Excel attachment found" in "\n".join(r["log_lines"]), f
        assert not any("\x00" in l for l in r["log_lines"])
    print("11 real .msg  OK  parsed", 3, "real Outlook files; non-Excel attachments listed, NULs stripped, no crash")
print("\nALL PASSED  (workspace:", TMP, ")")
