"""Same pipeline without the UI (for scheduling / automation):  python run_cli.py path/to/email.msg"""
import sys
from pathlib import Path

from extractor.logger import RunLogger
from extractor.pipeline import run_pipeline

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python run_cli.py <file.msg>")
    src = Path(sys.argv[1])
    report = run_pipeline(src.read_bytes(), src.name, log=RunLogger(on_line=print))
    sys.exit(0 if report["status"] == "success" else 1)
