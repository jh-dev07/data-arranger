"""Helpers shared by extractors."""
import html
import re
from typing import List, Optional

MAX_TEXT = 60000


def clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t　\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text[:MAX_TEXT]


def xml_unescape(s: str) -> str:
    return html.unescape(s or "")


def strip_tags(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|noscript|head)\b.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|h\d|li|tr|title|w:p|a:p|text:p|text:h)>", "\n", markup)
    markup = re.sub(r"<[^>]+>", " ", markup)
    return clean_text(html.unescape(markup))


def split_author_field(value: str) -> List[str]:
    """Split an author string such as "A. Smith; B. Jones" or "张三, 李四"."""
    value = (value or "").strip()
    if not value or value.lower() in ("unknown", "administrator", "admin", "user", "author", "owner"):
        return []
    if ";" in value or "；" in value:
        parts = re.split(r"[;；]", value)
    elif re.search(r"\s(?:and|&)\s", value):
        parts = re.split(r",\s*(?:and\s+)?|\s+(?:and|&)\s+", value)
    elif "，" in value or "、" in value:
        parts = re.split(r"[，、]", value)
    elif "," in value:
        parts = value.split(",")
        # "Smith, John" (one person, inverted) vs "John Smith, Jane Doe"
        if len(parts) == 2 and " " not in parts[0].strip() and " " not in parts[1].strip():
            parts = [value]
    else:
        parts = [value]
    out = []
    for p in parts:
        p = " ".join(p.split()).strip(" .")
        if p and p not in out and len(p) < 80:
            out.append(p)
    return out


def decode_bytes(data: bytes, hint: Optional[str] = None) -> str:
    """Decode text of unknown encoding: BOM -> hint -> UTF-8 -> charset
    detection -> GB18030 -> cp1252."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    if hint:
        try:
            return data.decode(hint)
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # UTF-16 without BOM: many NULs at alternating positions
    if len(data) > 20 and data[1::2].count(0) > len(data) * 0.3:
        return data.decode("utf-16-le", "replace")
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(data[:200000]).best()
        if best is not None and best.encoding:
            return data.decode(best.encoding, "replace")
    except Exception:
        pass
    try:
        return data.decode("gb18030")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")
