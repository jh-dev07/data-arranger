"""CNKI formats (CAJ, KDH, NH/HN, TEB, CAA) and Chaoxing PDG books.

What is recovered:
  * PDF served with a CNKI extension: read as PDF (text + OCR)
  * KDH: XOR-obfuscated PDF behind a fixed header; decoded in memory
  * CAJ: the embedded PDF objects are re-assembled into a PDF (missing page
    tree and catalog are rebuilt, MuPDF repairs the xref); then text or OCR
  * HN (thesis format): page count, table of contents, and any JPEG-encoded
    page images, which are OCR'd
  * PDG books: the copyright (leg), title (bok) and first pages are OCR'd,
    which yields the CIP block with ISBN, publisher and date
  * always: the CNKI download name "Title_Author1_Author2.caj"
"""
import io
import os
import re
import struct

from .. import ocr
from ..models import FileRecord
from ..textmeta import parse_filename, plausible_title
from . import pdf
from .common import clean_text, decode_bytes, strip_tags

KDH_KEY = b"FZHMEI"
KDH_OFFSET = 254
MAX_READ = 400 * 1024 * 1024


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


# --- CAJ: rebuild a PDF from the embedded objects -------------------------------

_OBJ_RE = re.compile(rb"(?<![0-9])(\d{1,7})\s+(\d{1,5})\s+obj\b")


def rebuild_pdf(data: bytes) -> bytes:
    """Re-assemble a PDF from loose 'N 0 obj ... endobj' segments.

    CAJ files contain PDF page objects whose /Parent page-tree nodes and the
    document catalog are missing. Those are recreated here; the result has
    no xref table, so MuPDF reconstructs it when opening.
    """
    objs = list(_OBJ_RE.finditer(data))
    if not objs:
        raise ValueError("no PDF objects found")
    start = objs[0].start()
    end = data.rfind(b"endobj")
    if end < start:
        raise ValueError("no endobj")
    body = data[start:end + 6]
    objs = list(_OBJ_RE.finditer(body))
    defined = {}
    for i, m in enumerate(objs):
        seg_end = objs[i + 1].start() if i + 1 < len(objs) else len(body)
        defined[int(m.group(1))] = body[m.end():seg_end]

    pages_by_parent = {}
    for num, seg in defined.items():
        head = seg[:4096]
        if re.search(rb"/Type\s*/Page(?![a-zA-Z])", head):
            pm = re.search(rb"/Parent\s+(\d+)\s+0\s+R", head)
            if pm:
                pages_by_parent.setdefault(int(pm.group(1)), []).append(num)

    missing = [p for p in pages_by_parent if p not in defined]
    if not pages_by_parent:
        raise ValueError("no page objects")
    next_id = max(defined) + 1
    extra = []
    if len(missing) == 1:
        root = missing[0]
        kids = sorted(pages_by_parent[root])
        extra.append(b"%d 0 obj\n<< /Type /Pages /Kids [%s] /Count %d >>\nendobj\n" % (
            root, b" ".join(b"%d 0 R" % k for k in kids), len(kids)))
    elif missing:
        root = next_id
        next_id += 1
        total = 0
        for p in sorted(missing):
            kids = sorted(pages_by_parent[p])
            total += len(kids)
            extra.append(b"%d 0 obj\n<< /Type /Pages /Parent %d 0 R /Kids [%s] /Count %d >>\nendobj\n" % (
                p, root, b" ".join(b"%d 0 R" % k for k in kids), len(kids)))
        extra.append(b"%d 0 obj\n<< /Type /Pages /Kids [%s] /Count %d >>\nendobj\n" % (
            root, b" ".join(b"%d 0 R" % p for p in sorted(missing)), total))
    else:
        # page tree exists; find its root (a Pages node without /Parent)
        root = None
        for num, seg in defined.items():
            head = seg[:4096]
            if re.search(rb"/Type\s*/Pages", head) and not re.search(rb"/Parent\s", head):
                root = num
                break
        if root is None:
            raise ValueError("page tree root not found")
    catalog = next_id
    extra.append(b"%d 0 obj\n<< /Type /Catalog /Pages %d 0 R >>\nendobj\n" % (catalog, root))
    return (b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + body + b"\n" + b"".join(extra) +
            b"trailer\n<< /Root %d 0 R /Size %d >>\n%%%%EOF\n" % (catalog, catalog + 1))


# --- image carving (HN pages, PDG pages) ------------------------------------------

def carve_images(data: bytes, limit: int = 6, min_side: int = 400):
    """Find JPEG/PNG images embedded in a proprietary container."""
    from PIL import Image
    found = []
    for magic in (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n"):
        pos = 0
        while len(found) < limit:
            i = data.find(magic, pos)
            if i < 0:
                break
            pos = i + 3
            try:
                im = Image.open(io.BytesIO(data[i:i + 40 * 1024 * 1024]))
                im.load()
            except Exception:
                continue
            if min(im.size) >= min_side:
                found.append((i, im))
                # skip past this image
                if magic.startswith(b"\xff\xd8"):
                    eoi = data.find(b"\xff\xd9", i)
                    pos = eoi + 2 if eoi > 0 else pos
    found.sort(key=lambda t: t[0])
    return [im for _, im in found[:limit]]


def _ocr_images(rec: FileRecord, images, label: str) -> str:
    if not images:
        return ""
    if not ocr.available():
        rec.notes.append("OCR unavailable: " + ocr.unavailable_reason())
        return ""
    texts = []
    for n, im in enumerate(images):
        try:
            lines = ocr.ocr_lines(im)
        except Exception as exc:
            rec.notes.append("OCR failed on %s page: %s" % (label, exc.__class__.__name__))
            continue
        if n == 0 and lines:
            t = ocr.title_from_lines(lines, im.size[1])
            if t and plausible_title(t):
                rec.extra.setdefault("layout_title", t)
        texts.append(ocr.lines_to_text(lines))
    if any(t.strip() for t in texts):
        rec.extra["ocr_pages"] = str(len(texts))
        if "OCR" not in rec.metadata_sources:
            rec.metadata_sources.append("OCR")
    return clean_text("\n".join(texts))


# --- main entry ----------------------------------------------------------------------

def extract(rec: FileRecord) -> str:
    _apply_filename(rec)
    with open(rec.path, "rb") as fh:
        data = fh.read(MAX_READ)
    head = data[:16]

    if head.startswith(b"%PDF"):
        rec.extra["actual_format"] = "PDF"
        return pdf.extract_bytes(rec, data, "PDF")

    if head.startswith(b"KDH "):
        rec.extra["actual_format"] = "KDH"
        return pdf.extract_bytes(rec, _kdh_decode(data), "KDH")

    if head.startswith(b"CAJ") or head.startswith(b"HN") or head[:1] == b"\xc8":
        kind = "CAJ" if head.startswith(b"CAJ") else ("HN" if head.startswith(b"HN") else "C8")
        rec.extra["actual_format"] = kind
        toc_text = _caj_header(rec, data, kind)
        text = ""
        if kind == "CAJ":
            try:
                text = pdf.extract_bytes(rec, rebuild_pdf(data), "CAJ")
            except Exception as exc:
                rec.notes.append("CAJ page data could not be rebuilt (%s)" % exc.__class__.__name__)
        if not text.strip():
            text = _ocr_images(rec, carve_images(data, limit=4), kind)
            if not text.strip():
                rec.notes.append("%s pages use an encoding that can't be decoded "
                                 "(e.g. JBIG); used TOC and file name" % kind)
        return clean_text(toc_text + "\n" + text)

    # Embedded PDF further inside the file (some TEB/CAJ variants)
    i = data.find(b"%PDF-")
    if 0 < i < 4096:
        rec.extra["actual_format"] = "PDF (embedded)"
        return pdf.extract_bytes(rec, data[i:], "PDF")

    if rec.ext == "caa" or b"<" in data[:200]:
        rec.notes.append("CAA is a CNKI download descriptor, not the full document")
        return strip_tags(decode_bytes(data[:256 * 1024]))
    text = _ocr_images(rec, carve_images(data, limit=4), rec.ext.upper())
    if not text:
        rec.notes.append("Unrecognised %s variant; used the file name" % rec.ext.upper())
    return text


def _caj_header(rec: FileRecord, data: bytes, kind: str) -> str:
    page_off, toc_off = {"CAJ": (0x10, 0x110), "HN": (0x90, 0x158), "C8": (0x08, 0x50)}[kind]
    fh = io.BytesIO(data)
    fh.seek(page_off)
    raw = fh.read(4)
    if len(raw) == 4:
        pages = struct.unpack("<i", raw)[0]
        if 0 < pages < 20000:
            rec.pages = pages
    toc = _read_toc(fh, toc_off)
    if not toc:
        return ""
    rec.extra["toc"] = " | ".join(t for _, t in toc[:40])
    first = toc[0][1]
    usable = plausible_title(first) and not re.match(
        r"(摘\s*要|abstract|目\s*录|contents|\d|第\s*\S+\s*[章节])", first, re.I)
    # CNKI truncates long titles in file names; prefer the full TOC title.
    if usable and rec.title and first.startswith(rec.title.rstrip(".… ")):
        rec.title = ""
    if usable:
        rec.set_if_empty("title", first, kind + " table of contents")
    return "\n".join(t for _, t in toc)


def _apply_filename(rec: FileRecord) -> None:
    hints = parse_filename(rec.path)
    if hints.get("title"):
        rec.set_if_empty("title", hints["title"], "file name")
    if hints.get("authors"):
        rec.set_if_empty("authors", hints["authors"], "file name")


# --- Chaoxing / SSReader PDG books -----------------------------------------------

_SS_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")
# Page roles by file-name prefix, in the order they are worth reading.
_PDG_ORDER = [("leg", "copyright page"), ("bok", "title page"), ("cov", "cover"),
              ("!0", "contents"), ("fow", "preface"), ("0", "body")]


def _open_pdg(path: str):
    from PIL import Image
    try:
        im = Image.open(path)
        im.load()
        return im
    except Exception:
        pass
    with open(path, "rb") as fh:
        data = fh.read(30 * 1024 * 1024)
    imgs = carve_images(data, limit=1, min_side=200)
    return imgs[0] if imgs else None


def extract_pdg_bundle(rec: FileRecord) -> str:
    folder = rec.path
    name = os.path.basename(folder.rstrip("\\/"))
    files = sorted(os.listdir(folder))
    pdgs = [f for f in files if f.lower().endswith(".pdg")]
    content = [f for f in pdgs if re.fullmatch(r"\d{6}\.pdg", f, re.I)]
    rec.pages = len(content) or len(pdgs)
    ss = _SS_RE.search(name)
    if ss:
        rec.extra["ss_number"] = ss.group(1)
    title = _SS_RE.sub("", name)
    title = re.sub(r"^[_\-\s]+|[_\-\s]+$", "", title)
    title = re.sub(r"[_]+", " ", title).strip()
    if plausible_title(title) or re.search(r"[一-龥]{2,}", title):
        rec.set_if_empty("title", title, "folder name")

    texts = []
    for f in files:
        if f.lower().endswith((".txt", ".nfo", ".ini")) and os.path.getsize(os.path.join(folder, f)) < 64 * 1024:
            with open(os.path.join(folder, f), "rb") as fh:
                info = decode_bytes(fh.read())
            texts.append(info)
            _apply_info_file(rec, info)

    picked = []
    for prefix, _role in _PDG_ORDER:
        for f in pdgs:
            if f.lower().startswith(prefix) and f not in picked:
                picked.append(f)
                break
        if len(picked) >= 4:
            break
    images, undecodable = [], 0
    for f in picked:
        im = _open_pdg(os.path.join(folder, f))
        if im is None:
            undecodable += 1
        else:
            images.append(im)
    if undecodable and not images:
        rec.notes.append("PDG pages are encrypted or in an unsupported encoding; used folder name")
    texts.append(_ocr_images(rec, images, "PDG"))
    return clean_text("\n".join(t for t in texts if t))


def _apply_info_file(rec: FileRecord, info: str) -> None:
    for key, attr in (("书名", "title"), ("作者", "authors"), ("出版社", "publisher"),
                      ("出版日期", "publish_date"), ("ISBN", "isbn")):
        m = re.search(key + r"\s*[:：=]\s*([^\n\r]+)", info, re.I)
        if not m:
            continue
        val = m.group(1).strip()
        if attr == "authors":
            rec.set_if_empty(attr, [a for a in re.split(r"[,，;；、\s]+", val) if a], "info file")
        elif attr == "isbn":
            rec.set_if_empty(attr, re.sub(r"[^\dX]", "", val.upper()), "info file")
        else:
            rec.set_if_empty(attr, val, "info file")
