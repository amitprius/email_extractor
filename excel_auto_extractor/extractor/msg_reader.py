"""Open a .msg (or .eml), follow emails nested inside emails at ANY depth, and save ONLY the Excel attachments.

Everything else (images, PDFs, signatures ...) is ignored and listed in the log.
Identical Excel files (same content) that appear in several layers, e.g. a forwarded chain, are saved once.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from pathlib import Path
from typing import Any, Optional

import extract_msg

from .logger import RunLogger
from .utils import (EXCEL_EXT, OLE_MAGIC, clean_text, human_size, safe_name, slug, sniff_excel_ext)

EMAIL_EXT = {".msg", ".eml"}


@dataclass
class RawAtt:
    filename: str
    data: Optional[bytes] = None
    nested: Any = None   # an embedded extract_msg message object
    note: str = ""       # why nothing could be read


@dataclass
class RawMail:
    meta: dict
    atts: list = field(default_factory=list)


@dataclass
class Extraction:
    nodes: dict = field(default_factory=dict)       # every email (root + nested)
    files: list = field(default_factory=list)       # saved Excel attachments
    ignored: list = field(default_factory=list)     # attachments that are not Excel
    duplicates: list = field(default_factory=list)  # Excel files skipped because identical content was already saved


def _first_email(text: str) -> str:
    m = re.search(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+", text or "")
    return m.group(0) if m else ""


def _g(obj: Any, name: str, default: str = "") -> str:
    try:
        v = getattr(obj, name, default)
        return clean_text(str(v)) if v is not None else default
    except Exception:  # some fields raise on odd encodings; a bad field must never stop the run
        return default


# ------------------------------------------------------------------ readers
def read_msg_obj(m: Any) -> RawMail:
    sender = _g(m, "sender")
    meta = {"subject": _g(m, "subject"), "sender": sender, "sender_email": _first_email(sender),
            "to": _g(m, "to"), "date": _g(m, "date")}
    atts: list[RawAtt] = []
    for i, att in enumerate(getattr(m, "attachments", None) or []):
        name = clean_text(str(getattr(att, "longFilename", None) or getattr(att, "shortFilename", None)
                              or getattr(att, "name", None) or f"attachment_{i}"))
        atype = getattr(getattr(att, "type", None), "name", str(getattr(att, "type", "?")))
        try:
            data = att.data
        except Exception as e:
            atts.append(RawAtt(name, note=f"could not be read: {e}"))
            continue
        if isinstance(data, (bytes, bytearray)):
            atts.append(RawAtt(name, data=bytes(data)))
        elif data is not None and hasattr(data, "attachments"):
            atts.append(RawAtt(name, nested=data))
        else:
            atts.append(RawAtt(name, note=f"no file data inside the .msg (type {atype}); "
                                          "OneDrive/SharePoint link attachments hold only a link, not the file"))
    return RawMail(meta, atts)


def read_eml_bytes(raw: bytes) -> RawMail:
    m = message_from_bytes(raw, policy=policy.default)
    sender = clean_text(str(m.get("From", "") or ""))
    meta = {"subject": clean_text(str(m.get("Subject", "") or "")), "sender": sender,
            "sender_email": _first_email(sender), "to": clean_text(str(m.get("To", "") or "")),
            "date": clean_text(str(m.get("Date", "") or ""))}
    atts: list[RawAtt] = []

    def collect(part) -> None:
        if part.get_content_type() == "message/rfc822":
            atts.append(RawAtt(part.get_filename() or "embedded.eml", data=part.get_payload(0).as_bytes()))
        elif part.is_multipart():
            for p in part.get_payload():
                collect(p)
        elif part.get_filename():
            atts.append(RawAtt(clean_text(part.get_filename()), data=part.get_payload(decode=True) or b""))

    collect(m)
    return RawMail(meta, atts)


def _open_msg(raw: bytes):
    try:
        return extract_msg.openMsg(raw)
    except Exception as e:
        if "tzlocal" in str(e) or type(e).__name__ == "TZError":  # machine without a time zone configured
            os.environ["TZ"] = "UTC"
            return extract_msg.openMsg(raw)
        raise


# ------------------------------------------------------------------ walker
class _State:
    def __init__(self, dest: Path, log: RunLogger) -> None:
        self.dest, self.log = dest, log
        self.out = Extraction()
        self.count = 0
        self.hashes: dict[str, str] = {}
        self.folders: set[str] = set()

    def folder(self, parent: str, depth: int, subject: str) -> str:
        base = f"{parent}/L{depth}_{slug(subject)}"
        name, i = base, 2
        while name in self.folders:
            name, i = f"{base}_{i}", i + 1
        self.folders.add(name)
        return name


def _unique(dirpath: Path, name: str) -> Path:
    p, i = dirpath / name, 1
    while p.exists():
        p, i = dirpath / f"{Path(name).stem}_{i}{Path(name).suffix}", i + 1
    return p


def _walk(raw: RawMail, st: _State, parent: Optional[str], depth: int, trail: list, folder: str) -> str:
    st.count += 1
    nid = f"n{st.count}"
    subject = raw.meta.get("subject") or "(no subject)"
    trail = trail + [subject]
    node = {"node_id": nid, "parent": parent, "depth": depth, "folder": folder, **raw.meta, "attachments": []}
    st.out.nodes[nid] = node
    pad = "    " * depth
    st.log.info(f"{pad}{'📧' if depth == 0 else '📨 nested email'} level {depth}: '{subject}' "
                f"from {raw.meta.get('sender') or '?'} — {len(raw.atts)} attachment(s)")

    def descend(child: RawMail) -> str:
        return _walk(child, st, nid, depth + 1, trail, st.folder(folder, depth + 1, child.meta.get("subject", "")))

    for att in raw.atts:
        fname = safe_name(att.filename, "attachment")
        ext = Path(fname).suffix.lower()
        entry: dict = {"filename": fname}
        try:
            if att.note:
                entry.update(kind="unreadable", note=att.note)
                st.out.ignored.append({"filename": fname, "reason": att.note, "email": subject})
                st.log.warn(f"{pad}  ⚠ '{fname}' not saved: {att.note}")
            elif att.nested is not None:
                entry.update(kind="email", child=descend(read_msg_obj(att.nested)))
            else:
                data = att.data or b""
                if ext == ".msg":
                    m = _open_msg(data)
                    try:
                        entry.update(kind="email", child=descend(read_msg_obj(m)))
                    finally:
                        m.close()
                elif ext == ".eml":
                    entry.update(kind="email", child=descend(read_eml_bytes(data)))
                else:
                    real_ext = ext if ext in EXCEL_EXT else sniff_excel_ext(data, fname)  # Excel hiding behind a wrong name?
                    if not real_ext or fname.startswith("~$"):
                        entry.update(kind="ignored")
                        st.out.ignored.append({"filename": fname, "reason": "not an Excel file", "email": subject})
                        st.log.info(f"{pad}  – ignored (not Excel): {fname} ({human_size(len(data))})")
                    else:
                        if real_ext != ext:
                            fname = (Path(fname).stem if ext else fname) + real_ext
                            st.log.info(f"{pad}  ↻ '{att.filename}' is an Excel file with a wrong name; saved as '{fname}'")
                        digest = hashlib.sha256(data).hexdigest()
                        if digest in st.hashes:
                            entry.update(kind="duplicate", same_as=st.hashes[digest])
                            st.out.duplicates.append({"filename": fname, "same_as": st.hashes[digest], "email": subject})
                            st.log.info(f"{pad}  = skipped duplicate: {fname} (identical to {st.hashes[digest]})")
                        else:
                            outdir = st.dest / Path(folder)
                            outdir.mkdir(parents=True, exist_ok=True)
                            target = _unique(outdir, fname)
                            target.write_bytes(data)
                            key = target.relative_to(st.dest).as_posix()
                            st.hashes[digest] = key
                            rec = {"file_key": key, "filename": target.name, "path": str(target), "size": len(data),
                                   "sha256": digest, "depth": depth, "node_id": nid, "email_trail": trail}
                            st.out.files.append(rec)
                            entry.update(kind="excel", file_key=key)
                            st.log.ok(f"{pad}  📗 Excel saved: {key} ({human_size(len(data))})")
        except Exception as e:  # one bad attachment must never stop the run
            entry.update(kind="error", error=str(e))
            st.log.error(f"{pad}  ✖ failed on attachment '{fname}': {type(e).__name__}: {e}")
        node["attachments"].append(entry)
    return nid


def extract_excels(raw: bytes, filename: str, dest: Path, log: RunLogger) -> Extraction:
    """dest = the run's downloads folder. Returns what was found."""
    dest.mkdir(parents=True, exist_ok=True)
    st = _State(dest, log)
    root_folder = safe_name(Path(filename).stem, "email")[:60]
    st.folders.add(root_folder)
    if raw[:8] == OLE_MAGIC:           # decide by content, not by file extension
        m = _open_msg(raw)
        try:
            _walk(read_msg_obj(m), st, None, 0, [], root_folder)
        finally:
            m.close()
    else:
        _walk(read_eml_bytes(raw), st, None, 0, [], root_folder)
    return st.out
