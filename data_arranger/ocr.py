"""OCR for image-only pages (scanned PDFs, CNKI image pages, PDG, DjVu).

Engine: RapidOCR (PaddleOCR PP-OCR models on ONNX Runtime). It is fully
offline, ships its models inside the Python package, and reads Chinese and
English (including mixed pages). The engine is created lazily, once per
process.
"""
import logging
import threading
from typing import List, NamedTuple, Optional

log = logging.getLogger(__name__)

ENABLED = True          # switched off by the worker after repeated crashes
_engine = None
_engine_error = None
_lock = threading.Lock()


class OcrLine(NamedTuple):
    text: str
    height: float     # glyph height in pixels (proxy for font size)
    top: float
    left: float
    score: float


def available() -> bool:
    return _get_engine() is not None


def unavailable_reason() -> str:
    if not ENABLED:
        return "disabled after repeated crashes on this PC"
    _get_engine()
    return _engine_error or ""


def _get_engine():
    global _engine, _engine_error
    if not ENABLED:
        return None
    if _engine is not None or _engine_error is not None:
        return _engine
    with _lock:
        if _engine is None and _engine_error is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                _engine = RapidOCR()
            except Exception as exc:  # missing package / broken runtime
                _engine_error = "%s: %s" % (exc.__class__.__name__, exc)
                log.warning("OCR unavailable: %s", _engine_error)
    return _engine


def _to_bgr_array(image):
    import numpy as np
    from PIL import Image
    if isinstance(image, (bytes, bytearray)):
        import io
        image = Image.open(io.BytesIO(image))
    if isinstance(image, Image.Image):
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        w, h = image.size
        scale = min(1.0, 2600.0 / max(w, h))
        if scale < 1.0:
            image = image.resize((int(w * scale), int(h * scale)))
        if image.mode == "L":
            image = image.convert("RGB")
        arr = np.asarray(image)
        return np.ascontiguousarray(arr[:, :, ::-1])
    return image


def ocr_lines(image) -> List[OcrLine]:
    """OCR one page image (PIL image, bytes, or BGR ndarray)."""
    engine = _get_engine()
    if engine is None:
        return []
    arr = _to_bgr_array(image)
    result, _elapsed = engine(arr)
    if not result:
        return []
    boxes = []
    for box, text, score in result:
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        if not text or score < 0.5:
            continue
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        boxes.append(OcrLine(text.strip(), max(ys) - min(ys), min(ys), min(xs), score))
    return _merge_rows(boxes)


def _merge_rows(boxes: List[OcrLine]) -> List[OcrLine]:
    """Join boxes that sit on the same visual row (left to right)."""
    boxes = sorted(boxes, key=lambda b: (b.top, b.left))
    rows = []
    for b in boxes:
        if rows:
            last = rows[-1]
            if abs(b.top - last[0].top) < max(6.0, 0.5 * min(b.height, last[0].height)):
                last.append(b)
                continue
        rows.append([b])
    out = []
    for row in rows:
        row.sort(key=lambda b: b.left)
        text = _join(row)
        out.append(OcrLine(text, max(b.height for b in row), row[0].top, row[0].left,
                           sum(b.score for b in row) / len(row)))
    return out


def _join(row) -> str:
    parts = []
    for b in row:
        if parts and parts[-1] and _is_ascii_word_end(parts[-1]) and _is_ascii_word_start(b.text):
            parts.append(" ")
        parts.append(b.text)
    return "".join(parts)


def _is_ascii_word_end(s):
    return s[-1].isascii() and s[-1].isalnum()


def _is_ascii_word_start(s):
    return bool(s) and s[0].isascii() and s[0].isalnum()


def lines_to_text(lines: List[OcrLine]) -> str:
    return "\n".join(l.text for l in lines)


def title_from_lines(lines: List[OcrLine], page_height: Optional[float] = None) -> str:
    """Largest-glyph rows in the upper part of the page form the title."""
    if not lines:
        return ""
    limit = (page_height or max(l.top for l in lines) + 1) * 0.6
    upper = [l for l in lines if l.top <= limit and len(l.text) >= 2]
    if not upper:
        return ""
    biggest = max(l.height for l in upper)
    picked = [l for l in upper if l.height >= biggest * 0.85]
    picked.sort(key=lambda l: l.top)
    # keep only consecutive rows (a title spans adjacent lines)
    block = [picked[0]]
    for l in picked[1:]:
        if l.top - block[-1].top <= block[-1].height * 2.6:
            block.append(l)
        else:
            break
    return " ".join(l.text for l in block).strip()
