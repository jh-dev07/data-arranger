"""Per-file analysis pipeline:

    extract embedded metadata + text (OCR for image pages)
    -> rule-based text heuristics -> file-name hints
    -> local ML model (metadata + doc type), verified against the text
    -> optional online lookup -> language -> document type -> review flags
"""
import logging
import os
import traceback
from dataclasses import dataclass

from . import classify, enrich, fusion, textmeta
from .extractors import cnki, get_extractor
from .language import detect_language, language_name, normalize_code
from .models import CAT_DOCUMENT, FileRecord

log = logging.getLogger(__name__)

MIN_CONTENT_CHARS = 200


@dataclass
class Options:
    online: bool = False
    ml_mode: str = "always"     # "always" | "auto" | "off"
    ocr: bool = True


def analyze(rec: FileRecord, online: bool = False, options: Options = None) -> FileRecord:
    opts = options or Options(online=online)
    text = ""
    try:
        if rec.is_bundle and rec.ext == "pdg":
            text = cnki.extract_pdg_bundle(rec)
        else:
            fn = get_extractor(rec.ext, rec.category)
            if fn is not None:
                text = fn(rec) or ""
    except Exception as exc:
        rec.notes.append("could not read content (%s: %s); analysed from the file name"
                         % (exc.__class__.__name__, str(exc)[:200]))
        log.debug("extract failed for %s\n%s", rec.path, traceback.format_exc())

    if rec.category == CAT_DOCUMENT:
        _document_metadata(rec, text, opts)
        _set_language(rec, text)
        rec.doc_type, rec.doc_type_confidence, rec.doc_type_reasons = classify.classify(rec, text)
        rec.review = fusion.review_reasons(rec, len(text.strip()))
    rec.status = "Needs review" if rec.review else "Analyzed"
    return rec


def _document_metadata(rec: FileRecord, text: str, opts: Options) -> None:
    try:
        textmeta.fill_from_text(rec, text)
    except Exception as exc:
        rec.notes.append("text analysis failed: %s" % exc)

    if opts.ml_mode != "off" and text.strip():
        if opts.ml_mode == "always" or _needs_ml(rec, text):
            _run_ml(rec, text)

    if not rec.title or not rec.authors:
        hints = textmeta.parse_filename(rec.path)
        rec.set_if_empty("title", hints.get("title", ""), "file name")
        rec.set_if_empty("authors", hints.get("authors", []), "file name")
        rec.set_if_empty("publish_date", hints.get("year", ""), "file name")
    if opts.online and (rec.doi or rec.isbn):
        enrich.enrich(rec)


def _needs_ml(rec: FileRecord, text: str) -> bool:
    _t, conf, _r = classify.classify(rec, text)
    title_src = rec.source_of("title")
    return (conf < 0.6 or not rec.title or title_src in ("text", "file name", "PDF layout")
            or not rec.authors)


def _run_ml(rec: FileRecord, text: str) -> None:
    from . import llm
    if not llm.available():
        note = "ML model unavailable (%s); rules only" % llm.unavailable_reason()
        if note not in rec.notes:
            rec.notes.append(note)
        return
    try:
        data = llm.extract(text, rec.name, rec.pages)
    except Exception as exc:
        rec.notes.append("ML model failed: %s" % exc.__class__.__name__)
        return
    if data:
        fusion.merge(rec, data, text)


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
    meta_text = " ".join(x for x in [rec.title, rec.abstract, " ".join(rec.keywords)] if x)
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
