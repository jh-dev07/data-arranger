"""Which optional engines are present (shown in the status bar / self-test)."""
import importlib.util
from collections import OrderedDict

from . import paths


def _has(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def capabilities(deep: bool = False):
    caps = OrderedDict()
    caps["OCR"] = _has("rapidocr_onnxruntime")
    caps["ML model"] = _has("llama_cpp") and paths.llm_model_path() is not None
    caps["Language model"] = _has("fasttext") and paths.lid_model_path() is not None
    caps["PDF"] = _has("fitz")
    caps["CHM"] = paths.tool("7z") is not None or paths.tool("hh") is not None
    caps["DjVu"] = paths.tool("ddjvu") is not None
    if deep:
        from . import language, llm, ocr
        caps["OCR"] = caps["OCR"] and ocr.available()
        caps["ML model"] = caps["ML model"] and llm.available()
        caps["Language model"] = caps["Language model"] and language.fasttext_available()
        caps["OCR error"] = ocr.unavailable_reason()
        caps["ML error"] = llm.unavailable_reason()
    return caps
