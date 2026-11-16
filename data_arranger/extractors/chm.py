"""Compiled HTML Help (.chm): title and language from the ITSF container.

The CHM directory and the #SYSTEM file are stored uncompressed, so the title
and Windows LCID can be read without an LZX decompressor. Page text is
LZX-compressed and is not decoded; language comes from the LCID and title.
"""
import struct

from ..language import LANG_CODEPAGE, LCID_MAP
from ..models import FileRecord


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
        entries = _directory(fh, dir_off)

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
    return title
