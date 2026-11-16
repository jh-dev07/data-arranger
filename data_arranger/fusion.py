"""Merge the ML model's answer with metadata found by rules.

Every value from the model is checked against the document text before it
is used. Identifiers must also pass their checksum. This stops the model
from introducing titles, names or numbers that aren't in the document.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List

from . import identifiers as ids
from .models import DOC_TYPES, FileRecord
from .textmeta import plausible_title

SRC = "ML model"
# Sources that are more trustworthy than the model for a given field.
_STRONG_SOURCES = ("XMP", "table of contents", "EPUB", "Office properties", "HTML meta",
                   "Crossref", "Open Library", "info file", "MOBI", "FB2", "DjVu metadata")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[\W_]+", "", s)


def in_text(value: str, text_norm: str, fuzzy: bool = True) -> bool:
    v = norm(value)
    if not v:
        return False
    if v in text_norm:
        return True
    if not fuzzy or len(v) < 6:
        return False
    # OCR noise: accept a near-identical window.
    n = len(v)
    first = v[:4]
    start = text_norm.find(first)
    tries = 0
    while start >= 0 and tries < 40:
        window = text_norm[start:start + n + 4]
        if SequenceMatcher(None, v, window).ratio() >= 0.88:
            return True
        start = text_norm.find(first, start + 1)
        tries += 1
    # Title broken by hyphenation/line numbers: most tokens present
    tokens = re.findall(r"[a-z0-9]{3,}|[一-鿿]{2}", unicodedata.normalize("NFKC", value).lower())
    if len(tokens) >= 3:
        hit = sum(1 for t in tokens if t in text_norm)
        return hit >= 0.85 * len(tokens)
    return False


def _clean(v) -> str:
    if v is None:
        return ""
    return " ".join(str(v).split()).strip(" ;,.:")


_WEAK_SOURCES = ("", "file name", "folder name", "text", "PDF layout")


def _replaceable(rec: FileRecord, field: str, text_norm: str) -> bool:
    """May the model's value replace the current one?

    Weak sources (file name, text heuristics, layout guesses) may be
    replaced. Embedded metadata is kept, except PDF info-dictionary values
    that don't appear in the document (often junk such as "Slide 1").
    """
    cur = getattr(rec, field)
    if not cur:
        return True
    src = rec.source_of(field)
    if src in _WEAK_SOURCES:
        return True
    if src.endswith(" info"):
        values = cur if isinstance(cur, list) else [cur]
        return not any(in_text(v, text_norm, fuzzy=False) for v in values)
    return False


def _strong(rec: FileRecord, field: str) -> bool:
    """Was this field filled from embedded metadata we trust more than ML?"""
    src = rec.source_of(field)
    return bool(getattr(rec, field)) and any(s in src for s in _STRONG_SOURCES)


def _set(rec: FileRecord, field: str, value):
    setattr(rec, field, value)
    rec.extra["field_src_" + field] = SRC
    if SRC not in rec.metadata_sources:
        rec.metadata_sources.append(SRC)


def merge(rec: FileRecord, data: Dict, text: str) -> None:
    if not data:
        return
    t = norm(text)
    used = []

    # --- doc type: passed to the classifier as a strong vote
    dt = data.get("doc_type")
    if dt in DOC_TYPES:
        rec.extra["type_from_llm"] = dt

    # --- title
    title = _clean(data.get("title"))
    if title and plausible_title(title) and len(title) <= 300 and in_text(title, t):
        if _replaceable(rec, "title", t) and norm(title) != norm(rec.title):
            _set(rec, "title", title)
            used.append("title")

    # --- authors
    authors = [a for a in (_clean(x) for x in data.get("authors") or []) if 1 < len(a) < 60]
    verified = [a for a in authors if in_text(a, t, fuzzy=False) or _name_in_text(a, t)]
    if verified and len(verified) >= 0.6 * len(authors):
        if _replaceable(rec, "authors", t) and verified != rec.authors:
            _set(rec, "authors", verified)
            used.append("authors")

    # --- simple text fields: fill if empty, replace if ours came from regex rules
    for field in ("journal", "conference", "publisher", "institution", "volume", "issue"):
        v = _clean(data.get(field))
        if not v or len(v) > 200:
            continue
        if field in ("volume", "issue"):
            if not re.fullmatch(r"[\dA-Za-z\-/ ]{1,12}", v) or not re.search(re.escape(v), text):
                continue
        elif not in_text(v, t):
            continue
        if not getattr(rec, field) or (not _strong(rec, field)
                                       and rec.source_of(field) != SRC):
            if getattr(rec, field) != v:
                _set(rec, field, v)
                used.append(field)

    degree = _clean(data.get("degree"))
    if degree and not rec.degree:
        low = degree.lower()
        label = ("Doctoral" if re.search(r"博士|doctor|ph\.?d", low) else
                 "Master" if re.search(r"硕士|master", low) else
                 "Bachelor" if re.search(r"学士|本科|bachelor", low) else "")
        if label:
            _set(rec, "degree", label)

    date = _clean(data.get("publish_date"))
    m = re.match(r"((?:1[5-9]|20)\d\d)(?:[-/.年](\d{1,2}))?(?:[-/.月](\d{1,2}))?", date)
    if m and m.group(1) in text and not rec.publish_date:
        _set(rec, "publish_date", "-".join(g.zfill(2) if i else g for i, g in enumerate(m.groups()) if g))
        used.append("date")

    # --- identifiers: checksum + present in text
    isbn = re.sub(r"[^\dXx]", "", _clean(data.get("isbn"))).upper()
    if isbn and not rec.isbn:
        found = ids.find_isbns("ISBN " + isbn)
        if found and found[0][-10:-1] in re.sub(r"[^\dX]", "", text.upper()):
            _set(rec, "isbn", found[0])
    issn = _clean(data.get("issn")).upper()
    if issn and not rec.issn:
        found = ids.find_issns("ISSN " + issn)
        if found and found[0].replace("-", "") in re.sub(r"[^\dX]", "", text.upper()):
            _set(rec, "issn", found[0])
    doi = _clean(data.get("doi"))
    if doi and not rec.doi:
        d = ids.find_doi(doi)
        if d and d.lower() in text.lower():
            _set(rec, "doi", d)

    # --- keywords
    kws = [k for k in (_clean(x) for x in data.get("keywords") or []) if 1 < len(k) < 60]
    kws = [k for k in kws if in_text(k, t, fuzzy=False)]
    if kws and not rec.keywords:
        _set(rec, "keywords", kws)

    # --- abstract: locate the start the model pointed at, take the paragraph
    start = _clean(data.get("abstract_start"))
    if start and not rec.abstract:
        ab = _abstract_from(text, start)
        if ab:
            _set(rec, "abstract", ab)

    if used:
        rec.extra["ml_fields"] = ", ".join(used)


def _name_in_text(name: str, t: str) -> bool:
    parts = [norm(p) for p in re.split(r"[\s.,]+", name) if len(norm(p)) >= 2]
    return len(parts) >= 2 and all(p in t for p in parts)


def _abstract_from(text: str, start: str) -> str:
    words = start.split()
    probe = " ".join(words[:6]) if len(words) > 1 else start[:12]
    idx = text.find(probe)
    if idx < 0:
        flat = " ".join(text.split())
        idx = flat.find(" ".join(probe.split()))
        if idx < 0:
            return ""
        text = flat
    chunk = text[idx:idx + 3000]
    end = re.search(r"\n\s*(?:key\s*words?|index\s+terms|关\s*键\s*词|关键字|1\.?\s+introduction|"
                    r"introduction|引\s*言|中图分类号|\[?KEY\s*WORDS)", chunk, re.I)
    if end:
        chunk = chunk[:end.start()]
    chunk = re.sub(r"-\n(?=[a-z])", "", chunk)
    chunk = " ".join(chunk.split())
    return chunk[:2500] if len(chunk) >= 60 else ""


def review_reasons(rec: FileRecord, text_len: int) -> List[str]:
    """Why a human should double-check this record (empty = looks fine)."""
    out = []
    if rec.category != "Documents":
        return out
    trusted_title = any(s in rec.source_of("title") for s in _STRONG_SOURCES)
    if text_len < 50 and not trusted_title:
        out.append("no readable text")
    if not rec.language:
        out.append("language unknown")
    if rec.doc_type_confidence < 0.45:
        out.append("doc type uncertain")
    src = rec.source_of("title")
    if not rec.title:
        out.append("no title")
    elif src in ("file name", "folder name"):
        out.append("title from file name")
    return out
