"""Optional online enrichment (off by default).

* Crossref for a DOI: authoritative title/authors/journal/volume/issue/
  date/publisher and work type (journal-article, proceedings-article,
  dissertation, standard, report, book...).
* Open Library for an ISBN: title/authors/publisher/date/pages.

Only the DOI or ISBN is sent; no file names or content leave the machine.
"""
import json
import re
import urllib.parse
import urllib.request

from .models import (BOOK, CONFERENCE, DISSERTATION, JOURNAL, REPORT,
                     STANDARD, FileRecord)

_UA = "DataArranger/1.0 (desktop file organizer)"
_TIMEOUT = 12

_CROSSREF_TYPES = {
    "journal-article": JOURNAL, "proceedings-article": CONFERENCE,
    "book": BOOK, "monograph": BOOK, "edited-book": BOOK, "reference-book": BOOK,
    "book-chapter": BOOK, "dissertation": DISSERTATION, "standard": STANDARD,
    "report": REPORT, "posted-content": REPORT,
}

_cache = {}


def _get_json(url: str):
    if url in _cache:
        return _cache[url]
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    _cache[url] = data
    return data


def _override(rec: FileRecord, attr: str, value, src: str):
    """Online catalog data is more reliable than heuristics: replace values
    that came from text heuristics or file names, keep the rest."""
    if not value:
        return
    if isinstance(value, str):
        value = " ".join(value.split())
    setattr(rec, attr, value)
    if src not in rec.metadata_sources:
        rec.metadata_sources.append(src)


def crossref(rec: FileRecord) -> bool:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(rec.doi, safe="/")
    msg = _get_json(url).get("message", {})
    src = "Crossref"
    title = (msg.get("title") or [""])[0]
    _override(rec, "title", re.sub(r"<[^>]+>", "", title), src)
    authors = []
    for a in msg.get("author", []):
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "")
        if name:
            authors.append(name)
    _override(rec, "authors", authors, src)
    container = (msg.get("container-title") or [""])[0]
    wtype = msg.get("type", "")
    if wtype == "proceedings-article":
        _override(rec, "conference", container, src)
    elif container:
        _override(rec, "journal", container, src)
    _override(rec, "volume", msg.get("volume", ""), src)
    _override(rec, "issue", msg.get("issue", ""), src)
    _override(rec, "publisher", msg.get("publisher", ""), src)
    issns = msg.get("ISSN") or []
    if issns:
        _override(rec, "issn", issns[0], src)
    isbns = msg.get("ISBN") or []
    if isbns and not rec.isbn:
        _override(rec, "isbn", re.sub(r"[^\dX]", "", isbns[0].upper()), src)
    parts = (msg.get("issued") or {}).get("date-parts") or [[]]
    if parts and parts[0] and parts[0][0]:
        _override(rec, "publish_date", "-".join("%02d" % p if i else str(p) for i, p in enumerate(parts[0])), src)
    if msg.get("subject"):
        rec.set_if_empty("keywords", msg["subject"][:15], src)
    if msg.get("abstract") and not rec.abstract:
        rec.abstract = re.sub(r"<[^>]+>", " ", msg["abstract"]).strip()
    mapped = _CROSSREF_TYPES.get(wtype)
    if mapped:
        rec.extra["type_from_lookup"] = mapped
    return True


def openlibrary(rec: FileRecord) -> bool:
    key = "ISBN:" + rec.isbn
    url = "https://openlibrary.org/api/books?format=json&jscmd=data&bibkeys=" + key
    book = _get_json(url).get(key)
    if not book:
        return False
    src = "Open Library"
    title = book.get("title", "")
    if book.get("subtitle"):
        title += ": " + book["subtitle"]
    _override(rec, "title", title, src)
    _override(rec, "authors", [a.get("name", "") for a in book.get("authors", []) if a.get("name")], src)
    pubs = [p.get("name", "") for p in book.get("publishers", [])]
    if pubs:
        _override(rec, "publisher", pubs[0], src)
    _override(rec, "publish_date", book.get("publish_date", ""), src)
    if book.get("number_of_pages") and not rec.pages:
        rec.pages = int(book["number_of_pages"])
    subjects = [s.get("name", "") for s in book.get("subjects", [])][:10]
    if subjects:
        rec.set_if_empty("keywords", subjects, src)
    rec.extra["type_from_lookup"] = BOOK
    return True


def enrich(rec: FileRecord) -> None:
    try:
        if rec.doi:
            crossref(rec)
        elif rec.isbn:
            openlibrary(rec)
    except Exception as exc:  # network errors must never stop a scan
        rec.notes.append("online lookup failed: %s" % exc.__class__.__name__)
