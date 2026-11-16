"""CNKI formats (CAJ, KDH, NH, TEB, CAA) and Chaoxing PDG books.

These are proprietary formats. Here is what can be read reliably without
CNKI's CAJViewer:
  * files that are really PDFs (CNKI often serves PDFs as .caj)
  * KDH: an XOR-obfuscated PDF behind a fixed header; decoded in memory
  * CAJ / HN: the page count and the table of contents, whose first entry is
    usually the paper title (GB18030 text)
  * the CNKI download name convention "Title_Author1_Author2.caj"
Everything else (page images in HN/PDG) would need OCR, which isn't done.
"""
import os
import re
import struct

from ..models import FileRecord
from ..textmeta import parse_filename, plausible_title
from . import pdf
from .common import decode_bytes, strip_tags

KDH_KEY = b"FZHMEI"
KDH_OFFSET = 254


def _kdh_decode(data: bytes) -> bytes:
    body = data[KDH_OFFSET:]
    key = KDH_KEY * (len(body) // len(KDH_KEY) + 1)
    return bytes(a ^ b for a, b in zip(body, key))


def _read_toc(fh, toc_offset: int):
    fh.seek(toc_offset)
    raw = fh.read(4)
    if len(raw) < 4:
        return []
    count = struct.unpack("<i", raw)[0]
    if not 0 < count < 2000:
        return []
    entries = []
    for _ in range(count):
        chunk = fh.read(308)
        if len(chunk) < 308:
            break
        title_b, _page_b, _a, _b, level = struct.unpack("<256s24s12s12si", chunk)
        title = title_b.split(b"\x00")[0].decode("gb18030", "ignore").strip()
        if title:
            entries.append((level, title))
    return entries


def extract(rec: FileRecord) -> str:
    _apply_filename(rec)
    with open(rec.path, "rb") as fh:
        head = fh.read(16)

        if head.startswith(b"%PDF"):
            rec.extra["actual_format"] = "PDF"
            return pdf.extract(rec)

        if head.startswith(b"KDH "):
            rec.extra["actual_format"] = "KDH"
            fh.seek(0)
            data = fh.read()
            try:
                return pdf.extract_bytes(rec, _kdh_decode(data), "KDH")
            except Exception as exc:
                rec.notes.append("KDH decode failed: %s" % exc)
                return ""

        if head.startswith(b"CAJ") or head.startswith(b"HN"):
            kind = "CAJ" if head.startswith(b"CAJ") else "HN"
            rec.extra["actual_format"] = kind
            page_off, toc_off = (0x10, 0x110) if kind == "CAJ" else (0x90, 0x158)
            fh.seek(page_off)
            raw = fh.read(4)
            if len(raw) == 4:
                pages = struct.unpack("<i", raw)[0]
                if 0 < pages < 20000:
                    rec.pages = pages
            toc = _read_toc(fh, toc_off)
            if toc:
                rec.extra["toc"] = " | ".join(t for _, t in toc[:40])
                first = toc[0][1]
                usable = plausible_title(first) and not re.match(
                    r"(摘\s*要|abstract|目\s*录|contents|\d|第\s*\S+\s*[章节])", first, re.I)
                # CNKI truncates long titles in file names; prefer the full
                # TOC title when the file-name title is a prefix of it.
                if usable and rec.title and first.startswith(rec.title.rstrip(".… ")):
                    rec.title = ""
                if usable:
                    rec.set_if_empty("title", first, kind + " table of contents")
            if kind == "HN":
                rec.notes.append("HN pages are images; text needs OCR")
            else:
                rec.notes.append("CAJ text streams are not decoded; using TOC and file name")
            return "\n".join(t for _, t in toc)

        # CAA files are small link/descriptor files, sometimes plain text/XML.
        fh.seek(0)
        data = fh.read(256 * 1024)
    if rec.ext == "caa" or b"<" in data[:200]:
        text = strip_tags(decode_bytes(data))
        rec.notes.append("CAA is a CNKI download descriptor, not the full document")
        return text
    rec.notes.append("Unrecognised %s variant; using file name only" % rec.ext.upper())
    return ""


def _apply_filename(rec: FileRecord) -> None:
    hints = parse_filename(rec.path)
    if hints.get("title"):
        rec.set_if_empty("title", hints["title"], "file name")
    if hints.get("authors"):
        rec.set_if_empty("authors", hints["authors"], "file name")


# --- Chaoxing / SSReader PDG books -----------------------------------------------

_SS_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")


def extract_pdg_bundle(rec: FileRecord) -> str:
    """rec.path is a folder of PDG page files (one book)."""
    folder = rec.path
    name = os.path.basename(folder.rstrip("\\/"))
    pdgs = [f for f in os.listdir(folder) if f.lower().endswith(".pdg")]
    # Content pages are numbered 000001.pdg..; cov/bok/leg/fow/!0 are front matter.
    content = [f for f in pdgs if re.fullmatch(r"\d{6}\.pdg", f, re.I)]
    rec.pages = len(content) or len(pdgs)
    ss = _SS_RE.search(name)
    if ss:
        rec.extra["ss_number"] = ss.group(1)
    title = _SS_RE.sub("", name)
    title = re.sub(r"[_\-\s]+$|^[_\-\s]+", "", title)
    title = re.sub(r"[_]+", " ", title).strip()
    if plausible_title(title) or re.search(r"[一-龥]{2,}", title):
        rec.set_if_empty("title", title, "folder name")
    # Some downloads include an info text file.
    for f in os.listdir(folder):
        if f.lower().endswith((".txt", ".nfo", ".ini")) and os.path.getsize(os.path.join(folder, f)) < 64 * 1024:
            with open(os.path.join(folder, f), "rb") as fh:
                info = decode_bytes(fh.read())
            for key, attr in (("书名", "title"), ("作者", "authors"), ("出版社", "publisher"),
                              ("出版日期", "publish_date"), ("ISBN", "isbn"), ("页数", None)):
                m = re.search(key + r"\s*[:：=]\s*([^\n\r]+)", info, re.I)
                if m and attr:
                    val = m.group(1).strip()
                    if attr == "authors":
                        rec.set_if_empty(attr, [a for a in re.split(r"[,，;；、\s]+", val) if a], "info file")
                    elif attr == "isbn":
                        rec.set_if_empty(attr, re.sub(r"[^\dX]", "", val.upper()), "info file")
                    else:
                        rec.set_if_empty(attr, val, "info file")
            return info
    rec.notes.append("PDG pages are images; text needs OCR")
    return ""
