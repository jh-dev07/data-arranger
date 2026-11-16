"""Per-file analysis pipeline:

    extract embedded metadata + text  ->  text heuristics  ->  file-name hints
    ->  (optional) online lookup  ->  language  ->  document type
"""
import logging
import os
import traceback

from . import classify, enrich, textmeta
from .extractors import cnki, get_extractor
from .language import detect_language, language_name, normalize_code
from .models import CAT_DOCUMENT, FileRecord

log = logging.getLogger(__name__)

MIN_CONTENT_CHARS = 200


def analyze(rec: FileRecord, online: bool = False) -> FileRecord:
    text = ""
    try:
        if rec.is_bundle and rec.ext == "pdg":
            text = cnki.extract_pdg_bundle(rec)
        else:
            fn = get_extractor(rec.ext, rec.category)
            if fn is not None:
                text = fn(rec) or ""
    except Exception as exc:
        rec.notes.append("could not read content: %s: %s" % (exc.__class__.__name__, exc))
        log.debug("extract failed for %s\n%s", rec.path, traceback.format_exc())

    if rec.category == CAT_DOCUMENT:
        try:
            textmeta.fill_from_text(rec, text)
        except Exception as exc:
            rec.notes.append("text analysis failed: %s" % exc)
        if not rec.title or not rec.authors:
            hints = textmeta.parse_filename(rec.path)
            rec.set_if_empty("title", hints.get("title", ""), "file name")
            rec.set_if_empty("authors", hints.get("authors", []), "file name")
            rec.set_if_empty("publish_date", hints.get("year", ""), "file name")
        if online and (rec.doi or rec.isbn):
            enrich.enrich(rec)

    _set_language(rec, text)

    if rec.category == CAT_DOCUMENT:
        rec.doc_type, rec.doc_type_confidence, rec.doc_type_reasons = classify.classify(rec, text)
    rec.status = "Analyzed"
    return rec


def _set_language(rec: FileRecord, text: str) -> None:
    candidates = []
    tag = normalize_code(rec.extra.get("language_tag", ""))
    content = (text or "").strip()
    if len(content) >= MIN_CONTENT_CHARS:
        candidates.append(("content", content, False))
    if tag:
        candidates.append(("file tag", None, tag))
    if content:
        candidates.append(("content", content, True))
    meta_text = " ".join(x for x in [rec.title, rec.abstract, " ".join(rec.keywords), rec.album] if x)
    if meta_text:
        candidates.append(("title/metadata", meta_text, True))
    base = os.path.splitext(rec.name)[0] if not rec.is_bundle else rec.name
    candidates.append(("file name", base.replace("_", " "), False))

    for source, sample, mode in candidates:
        if sample is None:
            code = mode
        else:
            code, _conf = detect_language(sample, short_ok=mode)
        if code:
            rec.language = code
            rec.language_name = language_name(code)
            rec.language_source = source
            return
    rec.language = ""
    rec.language_name = "Unknown"
    rec.language_source = ""
