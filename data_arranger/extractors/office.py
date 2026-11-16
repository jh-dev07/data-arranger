"""Office and e-book formats: DOCX/PPTX/XLSX, ODF, legacy DOC/PPT/XLS/WPS
(OLE2), RTF, EPUB, MOBI/AZW3 and FB2. Only the standard library and
`olefile` are used."""
import re
import struct
import zipfile
from xml.etree import ElementTree as ET

from ..language import LCID_MAP
from ..models import FileRecord
from ..textmeta import plausible_title
from .common import clean_text, decode_bytes, split_author_field, strip_tags

try:
    import olefile
    _HAVE_OLE = True
except Exception:  # pragma: no cover
    _HAVE_OLE = False

_MAX_XML = 4 * 1024 * 1024


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def _xml_fields(data: bytes):
    """Flatten an XML document into {localname: [texts]}."""
    out = {}
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return out
    for el in root.iter():
        txt = (el.text or "").strip()
        if txt:
            out.setdefault(_local(el.tag), []).append(txt)
        for k, v in el.attrib.items():
            out.setdefault("@" + _local(k), []).append(v)
    return out


def _read(zf: zipfile.ZipFile, name: str, limit: int = _MAX_XML) -> bytes:
    try:
        with zf.open(name) as fh:
            return fh.read(limit)
    except KeyError:
        return b""


def _apply_core(rec: FileRecord, f, src: str) -> None:
    title = (f.get("title") or [""])[0]
    if plausible_title(title):
        rec.set_if_empty("title", title, src)
    authors = f.get("creator") or f.get("initial-creator") or []
    if len(authors) == 1:
        authors = split_author_field(authors[0])
    rec.set_if_empty("authors", authors, src)
    kws = f.get("keywords") or f.get("keyword") or []
    if len(kws) == 1:
        kws = [k.strip() for k in re.split(r"[;,，；、]", kws[0]) if k.strip()]
    rec.set_if_empty("keywords", kws, src)
    desc = (f.get("description") or [""])[0]
    if len(desc) > 100:
        rec.set_if_empty("abstract", desc, src)
    subj = (f.get("subject") or [""])[0]
    if subj:
        rec.extra.setdefault("subject", subj[:300])
    lang = (f.get("language") or [""])[0]
    if lang:
        rec.extra.setdefault("language_tag", lang)
    created = (f.get("created") or f.get("creation-date") or [""])[0]
    if created:
        rec.extra.setdefault("file_created", created)


# --- OOXML ------------------------------------------------------------------

def extract_ooxml(rec: FileRecord) -> str:
    with zipfile.ZipFile(rec.path) as zf:
        names = zf.namelist()
        _apply_core(rec, _xml_fields(_read(zf, "docProps/core.xml")), "Office properties")
        app = _xml_fields(_read(zf, "docProps/app.xml"))
        for key in ("Pages", "Slides"):
            if app.get(key) and app[key][0].isdigit():
                rec.pages = int(app[key][0])
                break
        if app.get("Company"):
            rec.extra.setdefault("company", app["Company"][0])

        parts = []
        if "word/document.xml" in names:
            parts.append(_read(zf, "word/document.xml"))
        slides = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[-1]))
        if slides and rec.pages is None:
            rec.pages = len(slides)
        parts.extend(_read(zf, n) for n in slides[:30])
        if "xl/sharedStrings.xml" in names:
            parts.append(_read(zf, "xl/sharedStrings.xml", 1024 * 1024))
        text = "\n".join(strip_tags(p.decode("utf-8", "ignore")) for p in parts)
        if slides and not rec.title:
            first = strip_tags(_read(zf, slides[0]).decode("utf-8", "ignore"))
            line = next((l for l in first.splitlines() if l.strip()), "")
            if plausible_title(line.strip()):
                rec.set_if_empty("title", line.strip(), "first slide")
    return clean_text(text)


# --- OpenDocument -------------------------------------------------------------

def extract_odf(rec: FileRecord) -> str:
    with zipfile.ZipFile(rec.path) as zf:
        meta = _xml_fields(_read(zf, "meta.xml"))
        _apply_core(rec, meta, "ODF properties")
        for k in ("@page-count", "@object-count"):
            if meta.get(k) and meta[k][0].isdigit():
                rec.pages = int(meta[k][0])
                break
        return strip_tags(_read(zf, "content.xml").decode("utf-8", "ignore"))


# --- EPUB -------------------------------------------------------------------

def extract_epub(rec: FileRecord) -> str:
    with zipfile.ZipFile(rec.path) as zf:
        container = _read(zf, "META-INF/container.xml").decode("utf-8", "ignore")
        m = re.search(r'full-path="([^"]+)"', container)
        if not m:
            return ""
        opf_path = m.group(1)
        opf = _read(zf, opf_path)
        f = _xml_fields(opf)
        src = "EPUB metadata"
        _apply_core(rec, f, src)
        rec.set_if_empty("publisher", (f.get("publisher") or [""])[0], src)
        rec.set_if_empty("publish_date", (f.get("date") or [""])[0], src)
        for ident in f.get("identifier", []):
            digits = re.sub(r"[^\dX]", "", ident.upper())
            if len(digits) in (10, 13) and ("isbn" in ident.lower() or digits.startswith("97")):
                rec.set_if_empty("isbn", digits, src)
        if f.get("subject"):
            rec.set_if_empty("keywords", f["subject"][:15], src)

        # Spine order -> first few content documents for text
        opf_s = opf.decode("utf-8", "ignore")
        manifest = dict(re.findall(r'<(?:\w+:)?item\b[^>]*?id="([^"]+)"[^>]*?href="([^"]+)"', opf_s))
        manifest.update({i: h for h, i in re.findall(r'<(?:\w+:)?item\b[^>]*?href="([^"]+)"[^>]*?id="([^"]+)"', opf_s)})
        spine = re.findall(r'<(?:\w+:)?itemref\b[^>]*idref="([^"]+)"', opf_s)
        base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
        rec.pages = None
        texts = []
        for idref in spine[:8]:
            href = manifest.get(idref)
            if href:
                texts.append(strip_tags(_read(zf, base + href, 512 * 1024).decode("utf-8", "ignore")))
        return clean_text("\n".join(texts))


# --- FB2 --------------------------------------------------------------------

def extract_fb2(rec: FileRecord) -> str:
    with open(rec.path, "rb") as fh:
        data = fh.read(8 * 1024 * 1024)
    s = data.decode("utf-8", "ignore") if b'encoding="utf-8"' in data[:200].lower() else decode_bytes(data)
    src = "FB2 metadata"

    def tag(name, scope=s):
        m = re.search(r"<%s\b[^>]*>(.*?)</%s>" % (name, name), scope, re.S)
        return strip_tags(m.group(1)).strip() if m else ""

    ti = re.search(r"<title-info>(.*?)</title-info>", s, re.S)
    if ti:
        block = ti.group(1)
        rec.set_if_empty("title", tag("book-title", block), src)
        authors = []
        for a in re.findall(r"<author>(.*?)</author>", block, re.S):
            name = " ".join(x for x in (tag("first-name", a), tag("middle-name", a), tag("last-name", a)) if x)
            if name:
                authors.append(name)
        rec.set_if_empty("authors", authors, src)
        lang = tag("lang", block)
        if lang:
            rec.extra.setdefault("language_tag", lang)
        rec.set_if_empty("abstract", tag("annotation", block), src)
        kw = tag("keywords", block)
        if kw:
            rec.set_if_empty("keywords", [k.strip() for k in kw.split(",") if k.strip()], src)
    pi = re.search(r"<publish-info>(.*?)</publish-info>", s, re.S)
    if pi:
        block = pi.group(1)
        rec.set_if_empty("publisher", tag("publisher", block), src)
        rec.set_if_empty("publish_date", tag("year", block), src)
        rec.set_if_empty("isbn", re.sub(r"[^\dX]", "", tag("isbn", block).upper()), src)
    body = re.search(r"<body\b.*?>(.*)", s, re.S)
    return strip_tags(body.group(1)[:300000]) if body else ""


# --- MOBI / AZW3 ----------------------------------------------------------------

def extract_mobi(rec: FileRecord) -> str:
    with open(rec.path, "rb") as fh:
        data = fh.read(2 * 1024 * 1024)
    if len(data) < 86:
        return ""
    nrec = struct.unpack(">H", data[76:78])[0]
    if nrec < 1:
        return ""
    r0 = struct.unpack(">I", data[78:82])[0]
    if data[r0 + 16:r0 + 20] != b"MOBI":
        rec.set_if_empty("title", data[:32].split(b"\x00")[0].decode("latin-1"), "PDB name")
        return ""
    hdr_len = struct.unpack(">I", data[r0 + 20:r0 + 24])[0]
    enc = struct.unpack(">I", data[r0 + 28:r0 + 32])[0]
    codec = "utf-8" if enc == 65001 else "cp1252"
    name_off, name_len = struct.unpack(">II", data[r0 + 84:r0 + 92])
    src = "MOBI metadata"
    rec.set_if_empty("title", data[r0 + name_off:r0 + name_off + name_len].decode(codec, "ignore"), src)
    locale = struct.unpack(">I", data[r0 + 92:r0 + 96])[0]
    if locale & 0xFF:
        lang = LCID_MAP.get(locale & 0x3FF | 0x400)
        if lang:
            rec.extra.setdefault("language_tag", lang)
    exth_flag = struct.unpack(">I", data[r0 + 128:r0 + 132])[0]
    if exth_flag & 0x40:
        p = r0 + 16 + hdr_len
        if data[p:p + 4] == b"EXTH":
            count = struct.unpack(">I", data[p + 8:p + 12])[0]
            q = p + 12
            authors = []
            for _ in range(min(count, 500)):
                if q + 8 > len(data):
                    break
                rtype, rlen = struct.unpack(">II", data[q:q + 8])
                if rlen < 8:
                    break
                val = data[q + 8:q + rlen].decode(codec, "ignore").strip()
                q += rlen
                if rtype == 100:
                    authors.extend(split_author_field(val))
                elif rtype == 101:
                    rec.set_if_empty("publisher", val, src)
                elif rtype == 103:
                    rec.set_if_empty("abstract", strip_tags(val), src)
                elif rtype == 104:
                    rec.set_if_empty("isbn", re.sub(r"[^\dX]", "", val.upper()), src)
                elif rtype == 105:
                    rec.set_if_empty("keywords", [val], src)
                elif rtype == 106:
                    rec.set_if_empty("publish_date", val, src)
                elif rtype == 503:
                    rec.title = ""
                    rec.set_if_empty("title", val, src)
                elif rtype == 524:
                    rec.extra.setdefault("language_tag", val)
            rec.set_if_empty("authors", authors, src)
    rec.notes.append("MOBI text is compressed; language taken from metadata/title")
    return ""


# --- RTF ----------------------------------------------------------------------

def rtf_to_text(rtf: str, codepage: str = "cp1252") -> str:
    out = []
    i, n = 0, len(rtf)
    skip_depth = None
    depth = 0
    pending = bytearray()

    def flush():
        if pending:
            out.append(pending.decode(codepage, "ignore"))
            pending.clear()

    while i < n:
        c = rtf[i]
        if c == "{":
            depth += 1
            if skip_depth is None and re.match(r"\{\\\*|\{\\(fonttbl|colortbl|stylesheet|info|pict|header|footer|object)\b", rtf[i:i + 14]):
                skip_depth = depth
            i += 1
        elif c == "}":
            if skip_depth == depth:
                skip_depth = None
            depth -= 1
            i += 1
        elif c == "\\":
            m = re.match(r"\\'([0-9a-fA-F]{2})", rtf[i:i + 4])
            if m:
                if skip_depth is None:
                    pending.append(int(m.group(1), 16))
                i += 4
                continue
            m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", rtf[i:i + 40])
            if m:
                word, arg = m.group(1), m.group(2)
                if skip_depth is None:
                    if word == "u" and arg:
                        flush()
                        out.append(chr(int(arg) % 65536))
                        # skip the ANSI fallback character
                        nxt = i + m.end()
                        if nxt < n and rtf[nxt] == "\\" and rtf[nxt + 1:nxt + 2] == "'":
                            i = nxt + 4
                            continue
                        if nxt < n and rtf[nxt] not in "\\{}":
                            i = nxt + 1
                            continue
                    elif word in ("par", "line", "sect", "page", "row"):
                        flush()
                        out.append("\n")
                    elif word == "tab":
                        flush()
                        out.append("\t")
                i += m.end()
            else:
                if skip_depth is None and i + 1 < n and rtf[i + 1] in "\\{}":
                    flush()
                    out.append(rtf[i + 1])
                i += 2
        else:
            if skip_depth is None and c not in "\r\n":
                flush()
                out.append(c)
            i += 1
    flush()
    return "".join(out)


def _rtf_group(raw: str, control: str) -> str:
    """Return the text of the brace group starting with {<control> (balanced)."""
    m = re.search(r"\{" + re.escape(control) + r"(?![a-zA-Z])", raw)
    if not m:
        return ""
    depth, i = 0, m.start()
    while i < len(raw):
        c = raw[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return raw[m.start() + 1:i]
        i += 1
    return ""


def extract_rtf(rec: FileRecord) -> str:
    with open(rec.path, "rb") as fh:
        raw = fh.read(4 * 1024 * 1024).decode("latin-1")
    m = re.search(r"\\ansicpg(\d+)", raw[:2000])
    cp = "cp" + m.group(1) if m else "cp1252"
    if cp == "cp936":
        cp = "gb18030"
    try:
        "".encode(cp)
    except LookupError:
        cp = "cp1252"
    block = _rtf_group(raw, "\\info")
    if block:

        def field(name):
            body = _rtf_group(block, "\\" + name)
            return rtf_to_text(body[len(name) + 1:], cp).strip() if body else ""

        src = "RTF info"
        t = field("title")
        if plausible_title(t):
            rec.set_if_empty("title", t, src)
        rec.set_if_empty("authors", split_author_field(field("author")), src)
        kw = field("keywords")
        if kw:
            rec.set_if_empty("keywords", [k for k in re.split(r"[;,，；]\s*", kw) if k], src)
    return clean_text(rtf_to_text(raw, cp))


# --- Legacy OLE2: DOC / PPT / XLS / WPS -------------------------------------------

def _ole_meta(rec: FileRecord, ole) -> None:
    try:
        meta = ole.get_metadata()
    except Exception:
        return
    cp = meta.codepage or 1252
    codec = "gb18030" if cp == 936 else ("utf-16-le" if cp == 1200 else "cp%d" % cp)
    if cp == 65001:
        codec = "utf-8"

    def s(v):
        if v is None:
            return ""
        if isinstance(v, bytes):
            try:
                return v.decode(codec, "ignore").strip("\x00 ").strip()
            except LookupError:
                return v.decode("latin-1", "ignore").strip("\x00 ").strip()
        return str(v).strip()

    src = "Office properties"
    title = s(meta.title)
    if plausible_title(title):
        rec.set_if_empty("title", title, src)
    rec.set_if_empty("authors", split_author_field(s(meta.author)), src)
    kw = s(meta.keywords)
    if kw:
        rec.set_if_empty("keywords", [k for k in re.split(r"[;,，；、]\s*", kw) if k], src)
    comments = s(meta.comments)
    if len(comments) > 100:
        rec.set_if_empty("abstract", comments, src)
    if s(meta.subject):
        rec.extra.setdefault("subject", s(meta.subject)[:300])
    if s(meta.company):
        rec.extra.setdefault("company", s(meta.company))
    if meta.num_pages:
        rec.pages = int(meta.num_pages)
    elif getattr(meta, "slides", None):
        rec.pages = int(meta.slides)
    if meta.create_time:
        rec.extra.setdefault("file_created", str(meta.create_time))


def _doc_text(ole, rec: FileRecord) -> str:
    """Extract text from a Word 97-2003 binary via the FIB piece table."""
    wd = ole.openstream("WordDocument").read()
    if len(wd) < 0x1AA:
        return ""
    lid = struct.unpack("<H", wd[6:8])[0]
    if lid in LCID_MAP:
        rec.extra.setdefault("language_tag", LCID_MAP[lid])
    flags = struct.unpack("<H", wd[0x0A:0x0C])[0]
    if flags & 0x0100:
        rec.notes.append("Word document is encrypted")
        return ""
    table_name = "1Table" if flags & 0x0200 else "0Table"
    if not ole.exists(table_name):
        return ""
    table = ole.openstream(table_name).read()
    fc_clx, lcb_clx = struct.unpack("<II", wd[0x01A2:0x01AA])
    clx = table[fc_clx:fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:   # skip Prc entries
        cb = struct.unpack("<H", clx[pos + 1:pos + 3])[0]
        pos += 3 + cb
    if pos >= len(clx) or clx[pos] != 0x02:
        return ""
    lcb = struct.unpack("<I", clx[pos + 1:pos + 5])[0]
    plc = clx[pos + 5:pos + 5 + lcb]
    n = (lcb - 4) // 12
    cps = struct.unpack("<%dI" % (n + 1), plc[:4 * (n + 1)])
    out = []
    total = 0
    for i in range(n):
        pcd = plc[4 * (n + 1) + 8 * i:4 * (n + 1) + 8 * i + 8]
        fc = struct.unpack("<I", pcd[2:6])[0]
        length = cps[i + 1] - cps[i]
        if fc & 0x40000000:
            start = (fc & ~0x40000000) // 2
            out.append(wd[start:start + length].decode("cp1252", "ignore"))
        else:
            out.append(wd[fc:fc + 2 * length].decode("utf-16-le", "ignore"))
        total += length
        if total > 200000:
            break
    text = "".join(out)
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", " ", text.replace("\r", "\n").replace("\x07", "\t"))
    # Strip field codes such as "HYPERLINK ..." between \x13 and \x14
    return text


def _ppt_text(ole) -> str:
    data = ole.openstream("PowerPoint Document").read()
    out = []
    i, n = 0, len(data)
    while i + 8 <= n and len(out) < 2000:
        ver_inst, rtype, rlen = struct.unpack("<HHI", data[i:i + 8])
        if ver_inst & 0x0F == 0x0F:     # container: descend
            i += 8
            continue
        body = data[i + 8:i + 8 + rlen]
        if rtype == 0x0FA0:
            out.append(body.decode("utf-16-le", "ignore"))
        elif rtype == 0x0FA8:
            out.append(body.decode("cp1252", "ignore"))
        i += 8 + rlen
    return "\n".join(out)


def _ole_fallback_text(ole) -> str:
    """Pull readable UTF-16 runs out of any OLE stream (XLS, WPS, ...)."""
    out = []
    for entry in ole.listdir():
        try:
            data = ole.openstream(entry).read(2 * 1024 * 1024)
        except Exception:
            continue
        for m in re.finditer(rb"(?:[\x20-\x7e]\x00|[\x00-\xff][\x4e-\x9f]){6,}", data):
            s = m.group(0).decode("utf-16-le", "ignore")
            if sum(ch.isalpha() for ch in s) >= len(s) * 0.5:
                out.append(s)
        if sum(len(x) for x in out) > 60000:
            break
    return "\n".join(out)


def extract_ole(rec: FileRecord) -> str:
    if not _HAVE_OLE:
        rec.notes.append("olefile not installed; legacy Office content not read")
        return ""
    if not olefile.isOleFile(rec.path):
        # Some .doc files are really RTF/HTML/DOCX saved with the wrong extension.
        with open(rec.path, "rb") as fh:
            head = fh.read(8)
        if head.startswith(b"{\\rtf"):
            return extract_rtf(rec)
        if head.startswith(b"PK"):
            return extract_ooxml(rec)
        with open(rec.path, "rb") as fh:
            return strip_tags(decode_bytes(fh.read(2 * 1024 * 1024)))
    ole = olefile.OleFileIO(rec.path)
    try:
        _ole_meta(rec, ole)
        text = ""
        try:
            if ole.exists("WordDocument"):
                text = _doc_text(ole, rec)
            elif ole.exists("PowerPoint Document"):
                text = _ppt_text(ole)
        except Exception as exc:
            rec.notes.append("binary text parse failed: %s" % exc)
        if len(text.strip()) < 20:
            text = _ole_fallback_text(ole)
        return clean_text(text)
    finally:
        ole.close()
