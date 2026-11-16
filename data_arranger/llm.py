"""Local language model for metadata extraction and document-type
classification (fully offline).

Model: Qwen2.5-1.5B-Instruct (GGUF, Q4_K_M, ~1 GB), run on the CPU with
llama.cpp. The answer is constrained by a JSON schema (grammar-based
sampling), so the output is always valid JSON with the expected fields.
The model's values are then checked against the document text by
`fusion.py`; the model can't introduce identifiers or titles that aren't in
the document.
"""
import json
import logging
import os
import threading
from typing import Dict, Optional

from . import paths
from .models import DOC_TYPES

log = logging.getLogger(__name__)

N_CTX = 4096
MAX_OUTPUT_TOKENS = 450
TEXT_TOKEN_BUDGET = 2600

_llm = None
_error = None
_lock = threading.Lock()

SCHEMA = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": DOC_TYPES},
        "title": {"type": "string"},
        "authors": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "journal": {"type": "string"},
        "conference": {"type": "string"},
        "publisher": {"type": "string"},
        "publish_date": {"type": "string"},
        "volume": {"type": "string"},
        "issue": {"type": "string"},
        "isbn": {"type": "string"},
        "issn": {"type": "string"},
        "doi": {"type": "string"},
        "institution": {"type": "string"},
        "degree": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "abstract_start": {"type": "string"},
    },
    "required": ["doc_type", "title", "authors", "journal", "conference", "publisher",
                 "publish_date", "volume", "issue", "isbn", "issn", "doi",
                 "institution", "degree", "keywords", "abstract_start"],
}

SYSTEM = (
    "You are a meticulous librarian. You read the first pages of a document "
    "(sometimes OCR output with errors) and return its bibliographic metadata as JSON.\n"
    "Rules:\n"
    "- Copy values exactly as they appear in the document, in the document's own language. Never translate.\n"
    "- Use an empty string or empty list when a field is not present. Never guess or invent.\n"
    "- title: the main title of THIS document (not the journal, series, or a section heading). "
    "Join title lines broken across lines.\n"
    "- authors: person names only, without affiliations, degrees, numbers or symbols.\n"
    "- publish_date: as YYYY, YYYY-MM or YYYY-MM-DD.\n"
    "- abstract_start: the first 8 to 12 words of the abstract/summary text as written, or empty.\n"
    "- doc_type, choose one:\n"
    "  Journal Article = paper in a journal/periodical (has journal name, volume/issue, ISSN, DOI, received/accepted dates);\n"
    "  Book = monograph, textbook, handbook, edited book, e-book (ISBN, publisher, chapters, CIP data);\n"
    "  Dissertation-Thesis = PhD/Master/Bachelor thesis (degree, university, supervisor/导师, 学位论文);\n"
    "  Conference Paper = paper in conference proceedings/symposium/workshop;\n"
    "  Patent = patent document or application (patent number, claims, 专利);\n"
    "  Standard = national/international/industry standard (GB/T, ISO, IEC, ASTM, 标准);\n"
    "  Report = technical/research/government/annual report, white paper, working paper;\n"
    "  Newspaper = newspaper article;\n"
    "  Unclassified = anything else (letters, manuals, slides, notes, forms...)."
)


def available() -> bool:
    return _get() is not None


def unavailable_reason() -> str:
    _get()
    return _error or ""


def _threads() -> int:
    n = os.cpu_count() or 4
    return max(2, min(8, n - 1 if n > 4 else n))


def _get():
    global _llm, _error
    if _llm is not None or _error is not None:
        return _llm
    with _lock:
        if _llm is None and _error is None:
            path = paths.llm_model_path()
            if not path:
                _error = "model file not found in the models folder"
                return None
            try:
                from llama_cpp import Llama
                _llm = Llama(model_path=path, n_ctx=N_CTX, n_threads=_threads(),
                             n_batch=512, verbose=False, seed=0)
            except Exception as exc:
                _error = "%s: %s" % (exc.__class__.__name__, exc)
                log.warning("LLM unavailable: %s", _error)
    return _llm


def _trim_to_tokens(llm, text: str, budget: int) -> str:
    try:
        toks = llm.tokenize(text.encode("utf-8"), add_bos=False)
    except Exception:
        return text[:budget * 2]
    if len(toks) <= budget:
        return text
    cut = llm.detokenize(toks[:budget]).decode("utf-8", "ignore")
    return cut


def build_prompt(text: str, file_name: str, pages: Optional[int]) -> str:
    return ("File name: %s\nPages: %s\n\nDocument text:\n<<<\n%s\n>>>\n\n"
            "Return the JSON metadata for this document." % (file_name, pages or "unknown", text))


def extract(text: str, file_name: str, pages: Optional[int] = None) -> Optional[Dict]:
    llm = _get()
    if llm is None or not text.strip():
        return None
    head = text.strip()
    # Front pages carry most metadata; keep a little of the end (imprint/ISBN).
    tail = ""
    if len(head) > 9000:
        tail = "\n...\n" + head[-1200:]
        head = head[:8000]
    body = _trim_to_tokens(llm, head, TEXT_TOKEN_BUDGET - (300 if tail else 0)) + \
        (_trim_to_tokens(llm, tail, 300) if tail else "")
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": build_prompt(body, file_name, pages)}]
    with _lock:
        out = llm.create_chat_completion(
            messages=messages, temperature=0.0, top_p=1.0, max_tokens=MAX_OUTPUT_TOKENS,
            response_format={"type": "json_object", "schema": SCHEMA})
    content = out["choices"][0]["message"]["content"] or ""
    try:
        data = json.loads(content)
    except ValueError:
        # Output cut at max_tokens: keep what was complete.
        data = _salvage(content)
    return data if isinstance(data, dict) else None


def _salvage(content: str):
    for end in range(len(content), 0, -1):
        if content[end - 1] in ",[{":
            continue
        for closer in ("}", "]}", '"}', '"]}'):
            try:
                return json.loads(content[:end] + closer)
            except ValueError:
                pass
        if len(content) - end > 400:
            break
    return None
