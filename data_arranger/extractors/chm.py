"""Compiled HTML Help (.chm): title and language from the ITSF container.

The CHM directory and the #SYSTEM file are stored uncompressed, so the title
and Windows LCID are read directly. Page text is LZX-compressed; it is
extracted with the bundled 7-Zip (or Windows' hh.exe) into a temp folder.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

from .. import paths
from ..language import LANG_CODEPAGE, LCID_MAP
from ..models import FileRecord
from .common import clean_text, decode_bytes, strip_tags

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _encint(buf: bytes, pos: int):
    val = 0
    while True:
        b = buf[pos]
        pos += 1
        val = (val << 7) | (b & 0x7F)
        if not b & 0x80:
            return val, pos


def _directory(fh, dir_off: int):
    fh.seek(dir_off)
    itsp = fh.read(0x54)
    if itsp[:4] != b"ITSP":
        return {}
    hdr_len = struct.unpack("<I", itsp[8:12])[0]
    chunk_size = struct.unpack("<I", itsp[0x10:0x14])[0]
    n_chunks = struct.unpack("<I", itsp[0x2C:0x30])[0]
    entries = {}
    for i in range(min(n_chunks, 4096)):
        fh.seek(dir_off + hdr_len + i * chunk_size)
        chunk = fh.read(chunk_size)
        if chunk[:4] != b"PMGL":
            continue
        free = struct.unpack("<I", chunk[4:8])[0]
        pos, end = 0x14, chunk_size - free
        while pos < end:
            nlen, pos = _encint(chunk, pos)
            name = chunk[pos:pos + nlen].decode("utf-8", "ignore")
            pos += nlen
            section, pos = _encint(chunk, pos)
            offset, pos = _encint(chunk, pos)
            length, pos = _encint(chunk, pos)
            entries[name] = (section, offset, length)
    return entries


def extract(rec: FileRecord) -> str:
    with open(rec.path, "rb") as fh:
        hdr = fh.read(0x60)
        if hdr[:4] != b"ITSF":
            rec.notes.append("Not a valid CHM file")
            return ""
        version = struct.unpack("<I", hdr[4:8])[0]
        lcid = struct.unpack("<I", hdr[0x14:0x18])[0]
        dir_off, dir_len = struct.unpack("<QQ", hdr[0x48:0x58])
        if version >= 3:
            content_off = struct.unpack("<Q", hdr[0x58:0x60])[0]
        else:
            content_off = dir_off + dir_len
        try:
            entries = _directory(fh, dir_off)
        except (IndexError, struct.error, OSError):
            entries = {}

        lang = LCID_MAP.get(lcid & 0xFFFF, "")
        title = ""
        sysent = entries.get("/#SYSTEM")
        if sysent and sysent[0] == 0:
            fh.seek(content_off + sysent[1])
            data = fh.read(min(sysent[2], 1024 * 1024))
            pos = 4
            raw_title = b""
            while pos + 4 <= len(data):
                code, length = struct.unpack("<HH", data[pos:pos + 4])
                body = data[pos + 4:pos + 4 + length]
                pos += 4 + length
                if code == 3:
                    raw_title = body.split(b"\x00")[0]
                elif code == 4 and len(body) >= 4:
                    sys_lcid = struct.unpack("<I", body[:4])[0]
                    lang = LCID_MAP.get(sys_lcid & 0xFFFF, lang)
            if raw_title:
                codec = LANG_CODEPAGE.get(lang, "cp1252")
                title = raw_title.decode(codec, "ignore").strip()

    if lang:
        rec.extra["language_tag"] = lang
    if title:
        rec.set_if_empty("title", title, "CHM header")
    topics = [n for n in entries if n.lower().endswith((".htm", ".html"))]
    if topics:
        rec.pages = len(topics)
        rec.extra["chm_topics"] = str(len(topics))
    body = _page_text(rec, lang)
    return clean_text(title + "\n" + body)


def _page_text(rec: FileRecord, lang: str) -> str:
    """Decompress the help pages with 7-Zip (bundled) or Windows' hh.exe."""
    seven = paths.tool("7z")
    hh = paths.tool("hh") if sys.platform == "win32" else None
    if not seven and not hh:
        rec.notes.append("CHM page text needs 7-Zip or hh.exe; used header only")
        return ""
    out = tempfile.mkdtemp(prefix="chm_", dir=paths.work_dir())
    try:
        if seven:
            cmd = [seven, "x", "-y", "-o" + out, rec.path, "*.htm", "*.html", "-r"]
        else:
            cmd = [hh, "-decompile", out, rec.path]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=120, creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as exc:
            rec.notes.append("CHM extraction failed: %s" % exc.__class__.__name__)
            return ""
        pages = []
        for root, _dirs, files in os.walk(out):
            for f in files:
                if f.lower().endswith((".htm", ".html")):
                    p = os.path.join(root, f)
                    pages.append((os.path.getsize(p), p))
        # the largest pages carry the most prose
        pages.sort(reverse=True)
        texts, total = [], 0
        hint = LANG_CODEPAGE.get(lang)
        for _size, p in pages[:25]:
            with open(p, "rb") as fh:
                raw = fh.read(2 * 1024 * 1024)
            t = strip_tags(decode_bytes(raw, _charset(raw) or hint))
            texts.append(t)
            total += len(t)
            if total > 40000:
                break
        return "\n".join(texts)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def _charset(raw: bytes):
    import re
    m = re.search(rb"""charset\s*=\s*["']?([\w\-]+)""", raw[:4096], re.I)
    if not m:
        return None
    cs = m.group(1).decode("ascii", "ignore").lower()
    return "gb18030" if cs in ("gb2312", "gbk") else cs
