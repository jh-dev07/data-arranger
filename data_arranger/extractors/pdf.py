"""PDF (and PDFs recovered from KDH/CAJ) via PyMuPDF.

Reads the info dictionary, the XMP packet and the text of the first pages.
Pages without a usable text layer (scans, image-only PDFs, broken font
encodings) are rendered and OCR'd. The title is taken from the largest font
on page 1 (or the largest OCR glyphs) when embedded metadata has none.
"""
import io
import re
from typing import List

from .. import ocr
from ..models import FileRecord
from ..textmeta import plausible_title
from .common import clean_text, split_author_field, xml_unescape

try:
    import fitz  # PyMuPDF
    fitz.TOOLS.mupdf_display_errors(False)
    _HAVE_FITZ = True
except Exception:  # pragma: no cover
    _HAVE_FITZ = False

FRONT_PAGES = 4         # pages read from the front
OCR_ZOOM = 2.0          # 144 dpi; enough for body text, fast on CPU


def extract(rec: FileRecord) -> str:
    if not _HAVE_FITZ:
        rec.notes.append("PyMuPDF missing; PDF content not read")
        return ""
    doc = fitz.open(rec.path)
    try:
        return analyze_document(rec, doc, "PDF")
    finally:
        doc.close()


def extract_bytes(rec: FileRecord, data: bytes, label: str) -> str:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if doc.page_count == 0:
            raise ValueError("no pages")
        return analyze_document(rec, doc, label)
    finally:
        doc.close()


def _usable(text: str) -> bool:
    t = text.strip()
    if len(t) < 40:
        return False
    bad = sum(1 for ch in t if ch == "�" or 0xE000 <= ord(ch) <= 0xF8FF or ord(ch) < 9)
    letters = sum(1 for ch in t if ch.isalpha())
    return bad < len(t) * 0.1 and letters > len(t) * 0.3


def render_page(page, zoom: float = OCR_ZOOM):
    from PIL import Image
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    mode = "RGB" if pix.n >= 3 else "L"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def analyze_document(rec: FileRecord, doc, label: str) -> str:
    if doc.needs_pass and not doc.authenticate(""):
        rec.notes.append("PDF is password-protected; only the file name was used")
        return ""
    rec.pages = doc.page_count
    _read_info(rec, doc.metadata or {}, label)
    try:
        _read_xmp(rec, doc.get_xml_metadata() or "", label)
    except Exception:
        pass

    pages = list(range(min(FRONT_PAGES, doc.page_count)))
    if doc.page_count > FRONT_PAGES:
        pages.append(doc.page_count - 1)   # books: imprint/ISBN often at the back

    texts = []
    ocr_pages = 0
    layout_title = ""
    for i in pages:
        page = doc.load_page(i)
        text = page.get_text("text") or ""
        if _usable(text):
            if i == 0:
                layout_title = _title_from_spans(page)
        else:
            lines = _ocr_page(rec, page)
            if lines:
                ocr_pages += 1
                text = ocr.lines_to_text(lines)
                if i == 0:
                    layout_title = ocr.title_from_lines(lines, page.rect.height * OCR_ZOOM)
        texts.append(text)
    if ocr_pages:
        rec.extra["ocr_pages"] = str(ocr_pages)
        if "OCR" not in rec.metadata_sources:
            rec.metadata_sources.append("OCR")

    if layout_title and plausible_title(layout_title):
        rec.extra["layout_title"] = layout_title[:300]
        if not rec.title:
            rec.set_if_empty("title", layout_title, "PDF layout")
    text = clean_text("\n".join(texts))
    if not text.strip():
        rec.notes.append("no text found on the first pages, even with OCR")
    return text


def _ocr_page(rec: FileRecord, page):
    if not ocr.available():
        if "OCR unavailable" not in " ".join(rec.notes):
            rec.notes.append("OCR unavailable: " + ocr.unavailable_reason())
        return []
    try:
        return ocr.ocr_lines(render_page(page))
    except Exception as exc:
        rec.notes.append("OCR failed on page %d: %s" % (page.number + 1, exc.__class__.__name__))
        return []


def _title_from_spans(page) -> str:
    try:
        d = page.get_text("dict")
    except Exception:
        return ""
    height = page.rect.height or 1
    rows = []   # (size, top, text)
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            size = max(s.get("size", 0) for s in spans)
            top = line.get("bbox", [0, 0, 0, 0])[1]
            rows.append((size, top, "".join(s["text"] for s in spans).strip()))
    rows = [r for r in rows if r[1] < height * 0.6 and len(r[2]) >= 2]
    if not rows:
        return ""
    for size in sorted({round(r[0], 1) for r in rows}, reverse=True)[:3]:
        block = sorted((r for r in rows if round(r[0], 1) >= size - 0.05), key=lambda r: r[1])
        # consecutive lines only
        picked = [block[0]]
        for r in block[1:]:
            if r[1] - picked[-1][1] <= size * 2.2:
                picked.append(r)
            else:
                break
        cand = " ".join(r[2] for r in picked)
        cand = re.sub(r"\s+", " ", cand).strip()
        if plausible_title(cand) and len(cand) >= 6:
            return cand
    return ""


def _read_info(rec: FileRecord, info, label: str) -> None:
    src = label + " info"
    title = (info.get("title") or "").strip()
    if plausible_title(title):
        rec.extra["embedded_title"] = title
        rec.set_if_empty("title", title, src)
    rec.set_if_empty("authors", split_author_field(info.get("author") or ""), src)
    kw = (info.get("keywords") or "").strip()
    if kw:
        rec.set_if_empty("keywords", [k.strip() for k in re.split(r"[;,，；、]", kw) if k.strip()], src)
    subject = (info.get("subject") or "").strip()
    if subject:
        rec.extra.setdefault("pdf_subject", subject[:500])
        if len(subject) > 200:
            rec.set_if_empty("abstract", subject, src)
    for key, name in (("creator", "creator_app"), ("producer", "producer"),
                      ("creationDate", "file_created")):
        v = (info.get(key) or "").strip()
        if v:
            rec.extra.setdefault(name, v[:120])


def _xmp_values(xmp: str, tag: str) -> List[str]:
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


def _read_xmp(rec: FileRecord, xmp: str, label: str) -> None:
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
        rec.extra.setdefault("embedded_title", title)
        rec.set_if_empty("title", title, src)
    creators = _xmp_values(xmp, "dc:creator")
    if len(creators) == 1:
        creators = split_author_field(creators[0])
    rec.set_if_empty("authors", creators, src)
    rec.set_if_empty("keywords", _xmp_values(xmp, "dc:subject"), src)
    desc = first("dc:description")
    if len(desc) > 150:
        rec.set_if_empty("abstract", desc, src)
    rec.set_if_empty("doi", re.sub(r"^(?:doi:|https?://(?:dx\.)?doi\.org/)", "",
                                   first("prism:doi", "pdfx:doi", "crossmark:DOI")), src)
    rec.set_if_empty("journal", first("prism:publicationName"), src)
    rec.set_if_empty("volume", first("prism:volume"), src)
    rec.set_if_empty("issue", first("prism:number", "prism:issueIdentifier"), src)
    rec.set_if_empty("issn", first("prism:issn", "prism:eIssn"), src)
    rec.set_if_empty("isbn", re.sub(r"[^\dX]", "", first("prism:isbn").upper()), src)
    rec.set_if_empty("publisher", first("dc:publisher"), src)
    rec.set_if_empty("publish_date", first("prism:coverDate", "prism:publicationDate"), src)
    lang = first("dc:language")
    if lang:
        rec.extra.setdefault("language_tag", lang)
    agg = first("prism:aggregationType")
    if agg:
        rec.extra.setdefault("prism_type", agg)


def open_any(data: bytes):
    """Open bytes with MuPDF, which repairs broken cross-reference tables."""
    return fitz.open(stream=io.BytesIO(data).getvalue(), filetype="pdf")
