"""File-format identification by extension, with magic-byte sniffing as backup."""
import os
from typing import Optional, Tuple

from .models import CAT_AUDIO, CAT_DOCUMENT, CAT_IMAGE, CAT_OTHER, CAT_VIDEO

# extension -> (category, format label)
_EXT = {}


def _reg(category, label, *exts):
    for e in exts:
        _EXT[e] = (category, label)


# Documents
_reg(CAT_DOCUMENT, "PDF", "pdf")
_reg(CAT_DOCUMENT, "Word", "doc", "docx", "docm", "dot", "dotx", "wps", "wpt")
_reg(CAT_DOCUMENT, "RTF", "rtf")
_reg(CAT_DOCUMENT, "OpenDocument Text", "odt")
_reg(CAT_DOCUMENT, "PowerPoint", "ppt", "pptx", "pptm", "pps", "ppsx", "pot", "potx", "dps")
_reg(CAT_DOCUMENT, "OpenDocument Presentation", "odp")
_reg(CAT_DOCUMENT, "Spreadsheet", "xls", "xlsx", "xlsm", "ods", "et", "csv", "tsv")
# CNKI formats: CAJ, KDH, NH (theses), TEB, CAA (download link).
# "cag" is accepted as a common misspelling of CAJ.
_reg(CAT_DOCUMENT, "CAJ (CNKI)", "caj", "cag")
_reg(CAT_DOCUMENT, "KDH (CNKI)", "kdh")
_reg(CAT_DOCUMENT, "NH (CNKI)", "nh")
_reg(CAT_DOCUMENT, "TEB (CNKI)", "teb")
_reg(CAT_DOCUMENT, "CAA (CNKI)", "caa")
_reg(CAT_DOCUMENT, "PDG (Chaoxing)", "pdg")
_reg(CAT_DOCUMENT, "CHM", "chm")
_reg(CAT_DOCUMENT, "DjVu", "djvu", "djv")
_reg(CAT_DOCUMENT, "HTML", "html", "htm", "xhtml", "shtml")
_reg(CAT_DOCUMENT, "MHTML", "mht", "mhtml")
_reg(CAT_DOCUMENT, "XML", "xml")
_reg(CAT_DOCUMENT, "Text", "txt", "text", "md", "markdown", "rst", "log", "nfo")
_reg(CAT_DOCUMENT, "TeX", "tex", "bib")
_reg(CAT_DOCUMENT, "EPUB", "epub")
_reg(CAT_DOCUMENT, "MOBI", "mobi", "azw", "azw3", "prc")
_reg(CAT_DOCUMENT, "FB2", "fb2")
_reg(CAT_DOCUMENT, "PostScript", "ps", "eps")
_reg(CAT_DOCUMENT, "XPS", "xps", "oxps")
_reg(CAT_DOCUMENT, "UVZ/Other e-book", "uvz", "ceb", "nlc", "ebk")

# Images
_reg(CAT_IMAGE, "JPEG", "jpg", "jpeg", "jpe", "jfif")
_reg(CAT_IMAGE, "PNG", "png")
_reg(CAT_IMAGE, "BMP", "bmp", "dib")
_reg(CAT_IMAGE, "GIF", "gif")
_reg(CAT_IMAGE, "TIFF", "tif", "tiff")
_reg(CAT_IMAGE, "WebP", "webp")
_reg(CAT_IMAGE, "HEIC", "heic", "heif")
_reg(CAT_IMAGE, "SVG", "svg")
_reg(CAT_IMAGE, "Icon", "ico")
_reg(CAT_IMAGE, "Photoshop", "psd")
_reg(CAT_IMAGE, "RAW photo", "cr2", "cr3", "nef", "arw", "dng", "orf", "rw2", "raf")

# Video ("mnpeg" accepted as a misspelling of mpeg)
_reg(CAT_VIDEO, "MP4", "mp4", "m4v")
_reg(CAT_VIDEO, "AVI", "avi")
_reg(CAT_VIDEO, "MPEG", "mpg", "mpeg", "mnpeg", "mpe", "m2v", "vob", "ts", "m2ts", "mts")
_reg(CAT_VIDEO, "Matroska", "mkv")
_reg(CAT_VIDEO, "QuickTime", "mov", "qt")
_reg(CAT_VIDEO, "Windows Media Video", "wmv", "asf")
_reg(CAT_VIDEO, "Flash Video", "flv", "f4v")
_reg(CAT_VIDEO, "WebM", "webm")
_reg(CAT_VIDEO, "3GP", "3gp", "3g2")
_reg(CAT_VIDEO, "RealMedia", "rm", "rmvb")

# Audio
_reg(CAT_AUDIO, "WAV", "wav")
_reg(CAT_AUDIO, "WMA", "wma")
_reg(CAT_AUDIO, "MP3", "mp3")
_reg(CAT_AUDIO, "Ogg", "ogg", "oga", "opus")
_reg(CAT_AUDIO, "FLAC", "flac")
_reg(CAT_AUDIO, "AAC/M4A", "aac", "m4a", "m4b")
_reg(CAT_AUDIO, "APE", "ape")
_reg(CAT_AUDIO, "AIFF", "aif", "aiff")
_reg(CAT_AUDIO, "AMR", "amr")
_reg(CAT_AUDIO, "MIDI", "mid", "midi")
_reg(CAT_AUDIO, "RealAudio", "ra")

# Magic bytes for files with no or unknown extension.
_MAGIC = [
    (b"%PDF", "pdf"),
    (b"AT&TFORM", "djvu"),
    (b"ITSF", "chm"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "doc"),   # OLE; refined below
    (b"KDH ", "kdh"),
    (b"CAJ", "caj"),
    (b"HN", "nh"),
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF8", "gif"),
    (b"BM", "bmp"),
    (b"II*\x00", "tif"),
    (b"MM\x00*", "tif"),
    (b"ID3", "mp3"),
    (b"OggS", "ogg"),
    (b"fLaC", "flac"),
    (b"0&\xb2u\x8ef\xcf\x11", "wma"),   # ASF; audio vs video decided later
    (b"\x1aE\xdf\xa3", "mkv"),
    (b"\x00\x00\x01\xba", "mpg"),
    (b"\x00\x00\x01\xb3", "mpg"),
    (b"MAC ", "ape"),
    (b"{\\rtf", "rtf"),
]


def sniff_extension(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
    except OSError:
        return None
    if head[:4] == b"RIFF":
        kind = head[8:12]
        return {b"WAVE": "wav", b"AVI ": "avi", b"WEBP": "webp"}.get(kind)
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B "):
            return "m4a"
        if brand.startswith(b"qt"):
            return "mov"
        return "mp4"
    if head[:2] == b"PK":
        return _sniff_zip(path)
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            return ext
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if stripped.startswith((b"<!doctype html", b"<html")):
        return "html"
    if stripped.startswith(b"<?xml"):
        return "xml"
    return None


def _sniff_zip(path: str) -> Optional[str]:
    import zipfile
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "mimetype" in names:
                mt = zf.read("mimetype").decode("ascii", "ignore").strip()
                if mt == "application/epub+zip":
                    return "epub"
                if mt.endswith("opendocument.text"):
                    return "odt"
                if mt.endswith("opendocument.presentation"):
                    return "odp"
                if mt.endswith("opendocument.spreadsheet"):
                    return "ods"
            if "word/document.xml" in names:
                return "docx"
            if "ppt/presentation.xml" in names:
                return "pptx"
            if "xl/workbook.xml" in names:
                return "xlsx"
    except (zipfile.BadZipFile, OSError, KeyError):
        pass
    return None


def identify(path: str) -> Tuple[str, str, str]:
    """Return (extension, category, format label) for a file."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in _EXT:
        cat, label = _EXT[ext]
        return ext, cat, label
    sniffed = sniff_extension(path)
    if sniffed and sniffed in _EXT:
        cat, label = _EXT[sniffed]
        return sniffed, cat, label
    return ext, CAT_OTHER, (ext.upper() if ext else "Unknown")


def supported_extensions():
    """Mapping category -> sorted list of extensions (for the Help dialog)."""
    out = {}
    for ext, (cat, _label) in _EXT.items():
        out.setdefault(cat, []).append(ext)
    return {k: sorted(v) for k, v in out.items()}
