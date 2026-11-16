"""Images (Pillow + EXIF), audio and video (mutagen, hachoir fallback)."""
import logging
import re

from ..language import normalize_code
from ..models import FileRecord

try:
    from PIL import ExifTags, Image
    Image.MAX_IMAGE_PIXELS = None
    _HAVE_PIL = True
except Exception:  # pragma: no cover
    _HAVE_PIL = False

try:
    import mutagen
    _HAVE_MUTAGEN = True
except Exception:  # pragma: no cover
    _HAVE_MUTAGEN = False

try:
    from hachoir.metadata import extractMetadata
    from hachoir.parser import createParser
    from hachoir.core import config as _hcfg
    _hcfg.quiet = True
    logging.getLogger("hachoir").setLevel(logging.ERROR)
    _HAVE_HACHOIR = True
except Exception:  # pragma: no cover
    _HAVE_HACHOIR = False


def _xp(value) -> str:
    """Windows XP* EXIF tags are UTF-16LE byte tuples."""
    try:
        if isinstance(value, (tuple, list)):
            value = bytes(value)
        if isinstance(value, bytes):
            return value.decode("utf-16-le", "ignore").rstrip("\x00").strip()
        return str(value).strip()
    except Exception:
        return ""


def extract_image(rec: FileRecord) -> str:
    if not _HAVE_PIL or rec.ext in ("svg", "heic", "heif"):
        if rec.ext == "svg":
            with open(rec.path, "rb") as fh:
                data = fh.read(256 * 1024).decode("utf-8", "ignore")
            m = re.search(r"<title>(.*?)</title>", data, re.S)
            if m:
                rec.set_if_empty("title", m.group(1).strip(), "SVG")
            return re.sub(r"<[^>]+>", " ", data)
        return ""
    texts = []
    with Image.open(rec.path) as im:
        rec.width, rec.height = im.size
        rec.extra["mode"] = im.mode
        try:
            exif = im.getexif()
        except Exception:
            exif = {}
        if exif:
            named = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
            try:
                sub = exif.get_ifd(0x8769)  # Exif sub-IFD: DateTimeOriginal etc.
                named.update({ExifTags.TAGS.get(k, k): v for k, v in sub.items()})
            except Exception:
                pass
            src = "EXIF"
            date = named.get("DateTimeOriginal") or named.get("DateTime")
            if date:
                rec.set_if_empty("publish_date", str(date).replace(":", "-", 2), src)
            for key in ("Make", "Model"):
                if named.get(key):
                    rec.extra[key.lower()] = str(named[key]).strip("\x00 ")
            title = _xp(named.get("XPTitle")) or str(named.get("ImageDescription") or "").strip("\x00 ")
            if title and title.lower() not in ("default", "untitled"):
                rec.set_if_empty("title", title, src)
                texts.append(title)
            artist = _xp(named.get("XPAuthor")) or str(named.get("Artist") or "").strip("\x00 ")
            if artist:
                rec.set_if_empty("artist", artist, src)
            kw = _xp(named.get("XPKeywords"))
            if kw:
                rec.set_if_empty("keywords", [k for k in re.split(r"[;,]", kw) if k.strip()], src)
                texts.append(kw)
            comment = _xp(named.get("XPComment")) or _xp(named.get("UserComment"))
            if comment:
                texts.append(comment)
            if 0x8825 in exif:
                rec.extra["gps"] = "yes"
        # PNG text chunks
        info = getattr(im, "text", None) or {}
        if isinstance(info, dict):
            for k, v in info.items():
                if k.lower() in ("title", "description", "comment"):
                    texts.append(str(v))
                    if k.lower() == "title":
                        rec.set_if_empty("title", str(v), "PNG text")
    return "\n".join(texts)


_TAG_KEYS = {
    "title": ("TIT2", "\xa9nam", "title", "Title", "WM/Title", "INAM"),
    "artist": ("TPE1", "\xa9ART", "artist", "Author", "WM/AlbumArtist", "IART"),
    "album": ("TALB", "\xa9alb", "album", "WM/AlbumTitle", "IPRD"),
    "date": ("TDRC", "TYER", "\xa9day", "date", "year", "WM/Year", "ICRD"),
    "genre": ("TCON", "\xa9gen", "genre", "WM/Genre", "IGNR"),
    "language": ("TLAN", "language", "WM/Language"),
    "comment": ("COMM", "\xa9cmt", "comment", "Description", "ICMT"),
    "lyrics": ("USLT", "\xa9lyr", "lyrics", "WM/Lyrics"),
}


def _tag_text(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else ""
    if hasattr(v, "text"):
        t = v.text
        v = t[0] if isinstance(t, list) and t else t
    if hasattr(v, "value"):
        v = v.value
    return str(v).strip() if v is not None else ""


def extract_av(rec: FileRecord) -> str:
    texts = []
    got = False
    if _HAVE_MUTAGEN:
        try:
            mf = mutagen.File(rec.path)
        except Exception:
            mf = None
        if mf is not None:
            got = True
            info = getattr(mf, "info", None)
            if info is not None:
                if getattr(info, "length", None):
                    rec.duration = float(info.length)
                for attr in ("bitrate", "sample_rate", "channels"):
                    v = getattr(info, attr, None)
                    if v:
                        rec.extra[attr] = str(v)
                if getattr(info, "width", None):
                    rec.width, rec.height = info.width, info.height
            tags = mf.tags or {}
            src = "media tags"
            for field, keys in _TAG_KEYS.items():
                val = ""
                for k in keys:
                    try:
                        if k in tags:
                            val = _tag_text(tags[k])
                    except Exception:
                        continue
                    if not val:
                        # ID3 frames are keyed like "COMM::eng" / "USLT::eng"
                        try:
                            for tk in tags.keys():
                                if str(tk).startswith(k + ":"):
                                    val = _tag_text(tags[tk])
                                    break
                        except Exception:
                            pass
                    if val:
                        break
                if not val:
                    continue
                if field == "title":
                    rec.set_if_empty("title", val, src)
                    texts.append(val)
                elif field == "artist":
                    rec.set_if_empty("artist", val, src)
                elif field == "album":
                    rec.set_if_empty("album", val, src)
                    texts.append(val)
                elif field == "date":
                    rec.set_if_empty("publish_date", val, src)
                elif field == "language":
                    code = normalize_code(val)
                    if code:
                        rec.extra["language_tag"] = code
                elif field in ("comment", "lyrics"):
                    texts.append(val)
                else:
                    rec.extra[field] = val
    if _HAVE_HACHOIR and (not got or rec.duration is None or
                          (rec.category == "Videos" and rec.width is None)):
        _hachoir(rec, texts)
    return "\n".join(texts)


def _hachoir(rec: FileRecord, texts) -> None:
    try:
        parser = createParser(rec.path)
    except Exception:
        parser = None
    if not parser:
        return
    with parser:
        try:
            meta = extractMetadata(parser)
        except Exception:
            meta = None
    if not meta:
        return
    def get(key):
        try:
            return meta.get(key) if meta.has(key) else None
        except Exception:
            return None
    dur = get("duration")
    if dur is not None and rec.duration is None:
        rec.duration = dur.total_seconds()
    w, h = get("width"), get("height")
    if w and h and rec.width is None:
        rec.width, rec.height = int(w), int(h)
    t = get("title")
    if t:
        rec.set_if_empty("title", str(t), "media header")
        texts.append(str(t))
    lang = get("language")
    if lang:
        code = normalize_code(str(lang))
        if code:
            rec.extra.setdefault("language_tag", code)
    # Language of the first audio/subtitle track in MKV/MP4 headers
    try:
        for group in meta.iterGroups():
            if group.has("language"):
                code = normalize_code(str(group.get("language")))
                if code:
                    rec.extra.setdefault("language_tag", code)
                    break
    except Exception:
        pass
