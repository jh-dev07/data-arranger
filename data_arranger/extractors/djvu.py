"""DjVu: page count and metadata.

If DjVuLibre's command-line tools (djvused, djvutxt) are on PATH they are
used for metadata and the hidden text layer. Otherwise the IFF structure is
read directly for the page count and any uncompressed annotation/text chunks.
"""
import re
import shutil
import struct
import subprocess
import sys

from ..models import FileRecord
from .common import clean_text

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _run(args, timeout=30) -> str:
    try:
        out = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=timeout, creationflags=_NO_WINDOW)
        return out.stdout.decode("utf-8", "ignore")
    except (OSError, subprocess.SubprocessError):
        return ""


def _iff_scan(path: str, rec: FileRecord) -> str:
    with open(path, "rb") as fh:
        data = fh.read(64 * 1024 * 1024)
    if not data.startswith(b"AT&TFORM"):
        return ""
    kind = data[12:16]
    if kind == b"DJVU":
        rec.pages = 1
    else:
        rec.pages = len(re.findall(rb"FORM....DJVU", data, re.S)) or None
        m = data.find(b"DIRM")
        if m > 0 and not rec.pages:
            nfiles = struct.unpack(">H", data[m + 9:m + 11])[0]
            rec.pages = nfiles or None
    texts = []
    for m in re.finditer(rb"(TXTa|ANTa)(....)", data, re.S):
        size = struct.unpack(">I", m.group(2))[0]
        body = data[m.end():m.end() + min(size, 200000)]
        if m.group(1) == b"TXTa" and len(body) > 3:
            tlen = struct.unpack(">I", b"\x00" + body[:3])[0]
            texts.append(body[3:3 + tlen].decode("utf-8", "ignore"))
        else:
            texts.append(body.decode("utf-8", "ignore"))
        if sum(len(t) for t in texts) > 60000:
            break
    if not texts:
        rec.notes.append("DjVu text layer is compressed or absent; install DjVuLibre to read it")
    return "\n".join(texts)


def extract(rec: FileRecord) -> str:
    djvused = shutil.which("djvused")
    if djvused:
        n = _run([djvused, "-e", "n", rec.path]).strip()
        if n.isdigit():
            rec.pages = int(n)
        meta = _run([djvused, "-e", "print-meta", "-u", rec.path])
        fields = dict(re.findall(r'^(\w+)\s+"((?:[^"\\]|\\.)*)"', meta, re.M))
        src = "DjVu metadata"
        rec.set_if_empty("title", fields.get("title", ""), src)
        if fields.get("author"):
            rec.set_if_empty("authors", [a.strip() for a in re.split(r"[;,]| and ", fields["author"]) if a.strip()], src)
        rec.set_if_empty("publisher", fields.get("publisher", ""), src)
        rec.set_if_empty("publish_date", fields.get("year", ""), src)
        rec.set_if_empty("isbn", re.sub(r"[^\dX]", "", fields.get("isbn", "").upper()), src)
        djvutxt = shutil.which("djvutxt")
        if djvutxt:
            last = min(rec.pages or 5, 5)
            text = _run([djvutxt, "--page=1-%d" % last, rec.path], timeout=60)
            if text.strip():
                return clean_text(text)
    return clean_text(_iff_scan(rec.path, rec))
