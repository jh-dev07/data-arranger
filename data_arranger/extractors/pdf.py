"""PDF metadata and text: document info dictionary, XMP packet, and the
first pages' text (used for language, abstract, identifiers and title)."""
import io
import logging
import math
import re
from typing import List

from ..models import FileRecord
from ..textmeta import plausible_title, title_from_font_spans
from .common import clean_text, split_author_field, xml_unescape

try:
    from pypdf import PdfReader
    _HAVE_PYPDF = True
except Exception:  # pragma: no cover
    _HAVE_PYPDF = False

logging.getLogger("pypdf").setLevel(logging.ERROR)

TEXT_PAGES = 4          # pages read from the front for text analysis


def extract(rec: FileRecord) -> str:
    with open(rec.path, "rb") as fh:
        return extract_stream(rec, fh, "PDF")


def extract_bytes(rec: FileRecord, data: bytes, label: str) -> str:
    return extract_stream(rec, io.BytesIO(data), label)


def extract_stream(rec: FileRecord, stream, label: str) -> str:
    if not _HAVE_PYPDF:
        rec.notes.append("pypdf not installed; PDF content not read")
        return ""
    reader = PdfReader(stream, strict=False)
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                rec.notes.append("PDF is password-protected")
                return ""
        except Exception:
            rec.notes.append("PDF is encrypted with an unsupported method")
            return ""

    try:
        rec.pages = len(reader.pages)
    except Exception:
        pass

    _read_info(rec, reader, label)
    _read_xmp(rec, reader, label)

    texts = []
    spans = []
    n = min(TEXT_PAGES, rec.pages or 0)
    for i in range(n):
        page = reader.pages[i]
        if i == 0:
            order = [0]

            def visitor(text, cm, tm, font_dict, font_size, _o=order):
                if not text or not text.strip():
                    return
                try:
                    scale = math.hypot(tm[2], tm[3]) * math.hypot(cm[2], cm[3])
                except Exception:
                    scale = 1.0
                size = (font_size or 0) * (scale or 1.0)
                spans.append((text, size, _o[0]))
                _o[0] += 1
            try:
                texts.append(page.extract_text(visitor_text=visitor) or "")
            except Exception:
                texts.append("")
        else:
            try:
                texts.append(page.extract_text() or "")
            except Exception:
                texts.append("")
    # The last page of books often carries the CIP/ISBN/imprint data.
    if rec.pages and rec.pages > n:
        try:
            texts.append(reader.pages[rec.pages - 1].extract_text() or "")
        except Exception:
            pass

    text = clean_text("\n".join(texts))
    if len(text.strip()) < 50:
        rec.notes.append("No text layer (scanned/image PDF); OCR is not performed")
    elif not rec.title or not plausible_title(rec.title):
        t = title_from_font_spans(spans[:400])
        if t:
            rec.title = ""
            rec.set_if_empty("title", t, "PDF layout")
    return text


def _s(v) -> str:
    if v is None:
        return ""
    try:
        return str(v).strip().strip("\x00")
    except Exception:
        return ""


def _read_info(rec: FileRecord, reader, label: str) -> None:
    try:
        info = reader.metadata or {}
    except Exception:
        return
    src = label + " info"
    title = _s(info.get("/Title"))
    if plausible_title(title):
        rec.set_if_empty("title", title, src)
    rec.set_if_empty("authors", split_author_field(_s(info.get("/Author"))), src)
    kw = _s(info.get("/Keywords"))
    if kw:
        rec.set_if_empty("keywords", [k.strip() for k in re.split(r"[;,，；、]", kw) if k.strip()], src)
    subject = _s(info.get("/Subject"))
    if subject:
        # Publishers often put "Journal, Vol, pages. doi:..." in Subject.
        rec.extra.setdefault("pdf_subject", subject[:500])
        if len(subject) > 200:
            rec.set_if_empty("abstract", subject, src)
    for key, name in (("/Creator", "creator_app"), ("/Producer", "producer"),
                      ("/CreationDate", "file_created")):
        v = _s(info.get(key))
        if v:
            rec.extra.setdefault(name, v[:120])


def _xmp_raw(reader) -> str:
    try:
        root = reader.trailer["/Root"]
        meta = root.get("/Metadata")
        if meta is None:
            return ""
        data = meta.get_object().get_data()
        return data.decode("utf-8", "ignore")
    except Exception:
        return ""


def _xmp_values(xmp: str, tag: str) -> List[str]:
    # Either <tag>value</tag>, <tag><rdf:Seq><rdf:li>v</rdf:li>..., or tag="value"
    out = []
    m = re.search(r"<%s\b[^>]*>(.*?)</%s>" % (re.escape(tag), re.escape(tag)), xmp, re.S)
    if m:
        inner = m.group(1)
        lis = re.findall(r"<rdf:li\b[^>]*>(.*?)</rdf:li>", inner, re.S)
        out = lis if lis else [inner]
    else:
        m = re.search(r'\b%s="([^"]*)"' % re.escape(tag), xmp)
        if m:
            out = [m.group(1)]
    return [xml_unescape(re.sub(r"<[^>]+>", "", v)).strip() for v in out if v.strip()]


def _read_xmp(rec: FileRecord, reader, label: str) -> None:
    xmp = _xmp_raw(reader)
    if not xmp:
        return
    src = label + " XMP"

    def first(*tags):
        for t in tags:
            v = _xmp_values(xmp, t)
            if v:
                return v[0]
        return ""

    title = first("dc:title")
    if plausible_title(title):
        rec.set_if_empty("title", title, src)
    creators = _xmp_values(xmp, "dc:creator")
    if len(creators) == 1:
        creators = split_author_field(creators[0])
    rec.set_if_empty("authors", creators, src)
    rec.set_if_empty("keywords", _xmp_values(xmp, "dc:subject"), src)
    desc = first("dc:description")
    if len(desc) > 150:
        rec.set_if_empty("abstract", desc, src)
    rec.set_if_empty("doi", first("prism:doi", "pdfx:doi", "crossmark:DOI").replace("doi:", ""), src)
    rec.set_if_empty("journal", first("prism:publicationName"), src)
    rec.set_if_empty("volume", first("prism:volume"), src)
    rec.set_if_empty("issue", first("prism:number", "prism:issueIdentifier"), src)
    rec.set_if_empty("issn", first("prism:issn", "prism:eIssn"), src)
    isbn = re.sub(r"[^\dX]", "", first("prism:isbn").upper())
    rec.set_if_empty("isbn", isbn, src)
    rec.set_if_empty("publisher", first("dc:publisher"), src)
    rec.set_if_empty("publish_date", first("prism:coverDate", "prism:publicationDate", "dc:date"), src)
    lang = first("dc:language")
    if lang:
        rec.extra.setdefault("language_tag", lang)
    agg = first("prism:aggregationType")
    if agg:
        rec.extra.setdefault("prism_type", agg)
