"""HTML / MHTML / XML / plain text / TeX."""
import email
import email.policy
import re
from xml.etree import ElementTree as ET

from ..models import DISSERTATION, JOURNAL, FileRecord
from ..textmeta import plausible_title
from .common import clean_text, decode_bytes, split_author_field, strip_tags

_MAX = 8 * 1024 * 1024


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(_MAX)


def _meta_tags(html: str):
    tags = {}
    for m in re.finditer(r"<meta\b([^>]*)>", html, re.I):
        attrs = dict((k.lower(), v) for k, _q, v in
                     re.findall(r'([\w:.\-]+)\s*=\s*(["\'])(.*?)\2', m.group(1), re.S))
        key = (attrs.get("name") or attrs.get("property") or attrs.get("itemprop") or "").lower()
        if key and "content" in attrs:
            tags.setdefault(key, []).append(strip_tags(attrs["content"]).strip())
    return tags


def apply_html(rec: FileRecord, html: str) -> str:
    src = "HTML meta"
    tags = _meta_tags(html)

    def first(*keys):
        for k in keys:
            if tags.get(k) and tags[k][0]:
                return tags[k][0]
        return ""

    # Google Scholar (citation_*), Dublin Core and Open Graph conventions
    title = first("citation_title", "dc.title", "og:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = strip_tags(m.group(1)).strip() if m else ""
    if plausible_title(title):
        rec.set_if_empty("title", title, src)
    authors = tags.get("citation_author") or tags.get("dc.creator") or []
    if not authors and first("author"):
        authors = split_author_field(first("author"))
    rec.set_if_empty("authors", authors, src)
    kw = first("citation_keywords", "keywords", "dc.subject")
    if kw:
        rec.set_if_empty("keywords", [k.strip() for k in re.split(r"[;,，；、]", kw) if k.strip()][:15], src)
    rec.set_if_empty("abstract", first("citation_abstract", "dc.description", "description"), src)
    rec.set_if_empty("journal", first("citation_journal_title"), src)
    rec.set_if_empty("conference", first("citation_conference_title"), src)
    rec.set_if_empty("volume", first("citation_volume"), src)
    rec.set_if_empty("issue", first("citation_issue"), src)
    rec.set_if_empty("issn", first("citation_issn"), src)
    rec.set_if_empty("isbn", re.sub(r"[^\dX]", "", first("citation_isbn").upper()), src)
    rec.set_if_empty("doi", first("citation_doi", "dc.identifier").replace("doi:", ""), src)
    rec.set_if_empty("publisher", first("citation_publisher", "dc.publisher"), src)
    rec.set_if_empty("publish_date", first("citation_publication_date", "citation_date", "dc.date"), src)
    rec.set_if_empty("institution", first("citation_dissertation_institution"), src)
    if first("citation_dissertation_institution"):
        rec.extra.setdefault("type_from_lookup", DISSERTATION)
    if first("citation_patent_number"):
        rec.set_if_empty("patent_number", first("citation_patent_number"), src)
    m = re.search(r"<html\b[^>]*\blang\s*=\s*[\"']?([\w\-]+)", html, re.I)
    if m:
        rec.extra.setdefault("language_tag", m.group(1))
    return strip_tags(html)


def _charset(raw: bytes):
    m = re.search(rb"""<meta[^>]+charset\s*=\s*["']?([\w\-]+)""", raw[:4096], re.I)
    if not m:
        m = re.search(rb"""<\?xml[^>]+encoding\s*=\s*["']([\w\-]+)""", raw[:200], re.I)
    if m:
        cs = m.group(1).decode("ascii", "ignore").lower()
        return "gb18030" if cs in ("gb2312", "gbk") else cs
    return None


def extract_html(rec: FileRecord) -> str:
    raw = _read(rec.path)
    return clean_text(apply_html(rec, decode_bytes(raw, _charset(raw))))


def extract_mhtml(rec: FileRecord) -> str:
    raw = _read(rec.path)
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            cs = part.get_content_charset() or _charset(payload)
            return clean_text(apply_html(rec, decode_bytes(payload, cs)))
    return ""


# --- XML (JATS, TEI, Dublin Core, NLM, generic) ---------------------------------

def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def extract_xml(rec: FileRecord) -> str:
    raw = _read(rec.path)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return clean_text(strip_tags(decode_bytes(raw)))
    src = "XML"
    by_tag = {}
    for el in root.iter():
        by_tag.setdefault(_local(el.tag).lower(), []).append(el)

    def text_of(el):
        return " ".join("".join(el.itertext()).split())

    def first(*names):
        for n in names:
            for el in by_tag.get(n, []):
                t = text_of(el)
                if t:
                    return t
        return ""

    root_name = _local(root.tag).lower()
    rec.extra.setdefault("xml_root", root_name)
    title = first("article-title", "book-title", "title", "dc:title", "titlestmt")
    if plausible_title(title):
        rec.set_if_empty("title", title[:400], src)
    authors = []
    for c in by_tag.get("contrib", []) + by_tag.get("author", []) + by_tag.get("creator", []):
        sn = next((text_of(e) for e in c.iter() if _local(e.tag) == "surname"), "")
        gn = next((text_of(e) for e in c.iter() if _local(e.tag) in ("given-names", "forename")), "")
        name = (gn + " " + sn).strip() if sn else text_of(c)
        if name and len(name) < 80 and name not in authors:
            authors.append(name)
    rec.set_if_empty("authors", authors[:30], src)
    rec.set_if_empty("journal", first("journal-title"), src)
    rec.set_if_empty("volume", first("volume"), src)
    rec.set_if_empty("issue", first("issue"), src)
    rec.set_if_empty("issn", first("issn"), src)
    rec.set_if_empty("isbn", re.sub(r"[^\dX]", "", first("isbn").upper()), src)
    rec.set_if_empty("publisher", first("publisher-name", "publisher"), src)
    rec.set_if_empty("abstract", first("abstract"), src)
    kws = [text_of(k) for k in by_tag.get("kwd", []) + by_tag.get("keyword", []) + by_tag.get("subject", [])]
    rec.set_if_empty("keywords", [k for k in kws if k][:15], src)
    year = first("year", "date", "pubdate")
    rec.set_if_empty("publish_date", year, src)
    for el in by_tag.get("article-id", []):
        if el.get("pub-id-type") == "doi":
            rec.set_if_empty("doi", text_of(el), src)
    lang = root.get("{http://www.w3.org/XML/1998/namespace}lang") or root.get("lang") or first("language")
    if lang:
        rec.extra.setdefault("language_tag", lang)
    if root_name == "article" and by_tag.get("journal-title"):
        rec.extra.setdefault("type_from_lookup", JOURNAL)
    return clean_text(" ".join(t.strip() for t in root.itertext() if t.strip()))


# --- Plain text / TeX --------------------------------------------------------------

def extract_text(rec: FileRecord) -> str:
    raw = _read(rec.path)
    text = decode_bytes(raw)
    if rec.ext in ("tex", "bib"):
        src = "TeX"
        m = re.search(r"\\title\s*(?:\[[^\]]*\])?\{(.+?)\}\s*\n", text, re.S)
        if m:
            rec.set_if_empty("title", re.sub(r"\\\w+|[{}]", "", m.group(1)), src)
        m = re.search(r"\\author\s*\{(.+?)\}\s*\n", text, re.S)
        if m:
            rec.set_if_empty("authors", split_author_field(re.sub(r"\\\w+|[{}]", " ", m.group(1)).replace("\\\\", ";")), src)
        m = re.search(r"\\begin\{abstract\}(.+?)\\end\{abstract\}", text, re.S)
        if m:
            rec.set_if_empty("abstract", m.group(1), src)
    return clean_text(text)
