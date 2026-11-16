"""Language identification.

Strategy:
1. Count characters per writing system. CJK, Korean, Thai, Arabic, Hebrew,
   Greek and similar scripts identify the language (or a narrow family) by
   themselves, and this works even for short strings such as file names.
2. For Latin and Cyrillic text, use `langdetect` when installed, falling
   back to a stop-word vote so the program still works without it.
3. Short Latin text (e.g. a file name) is not guessed: it is reported as
   unknown rather than risking a wrong label.
"""
import re
from collections import Counter
from typing import Optional, Tuple

_fasttext_model = None
_fasttext_failed = False


def _fasttext():
    """fastText lid.176 language-ID model (176 languages), loaded once."""
    global _fasttext_model, _fasttext_failed
    if _fasttext_model is not None or _fasttext_failed:
        return _fasttext_model
    try:
        from . import paths
        path = paths.lid_model_path()
        if not path:
            raise FileNotFoundError("lid.176.ftz not found")
        import fasttext
        fasttext.FastText.eprint = lambda *a, **k: None   # silence warning
        _fasttext_model = fasttext.load_model(path)
    except Exception:
        _fasttext_failed = True
    return _fasttext_model


def fasttext_available() -> bool:
    return _fasttext() is not None


try:  # optional dependency
    from langdetect import DetectorFactory, detect_langs
    from langdetect.lang_detect_exception import LangDetectException
    DetectorFactory.seed = 0
    _HAVE_LANGDETECT = True
except Exception:  # pragma: no cover - depends on environment
    _HAVE_LANGDETECT = False

LANGUAGE_NAMES = {
    "zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean",
    "ru": "Russian", "uk": "Ukrainian", "bg": "Bulgarian", "sr": "Serbian",
    "mk": "Macedonian", "be": "Belarusian", "kk": "Kazakh", "mn": "Mongolian",
    "de": "German", "fr": "French", "es": "Spanish", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "sv": "Swedish", "da": "Danish",
    "no": "Norwegian", "fi": "Finnish", "pl": "Polish", "cs": "Czech",
    "sk": "Slovak", "hu": "Hungarian", "ro": "Romanian", "tr": "Turkish",
    "el": "Greek", "ar": "Arabic", "fa": "Persian", "ur": "Urdu",
    "he": "Hebrew", "hi": "Hindi", "mr": "Marathi", "ne": "Nepali",
    "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "th": "Thai",
    "vi": "Vietnamese", "id": "Indonesian", "ms": "Malay", "tl": "Tagalog",
    "hr": "Croatian", "sl": "Slovenian", "lt": "Lithuanian", "lv": "Latvian",
    "et": "Estonian", "ca": "Catalan", "af": "Afrikaans", "sw": "Swahili",
    "so": "Somali", "sq": "Albanian", "cy": "Welsh", "ka": "Georgian",
    "hy": "Armenian", "km": "Khmer", "lo": "Lao", "my": "Burmese",
    "am": "Amharic", "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam",
    "pa": "Punjabi", "si": "Sinhala", "bo": "Tibetan", "ug": "Uyghur",
    "la": "Latin", "eo": "Esperanto",
}

# Unicode ranges -> script name
_SCRIPT_RANGES = [
    (0x3040, 0x30FF, "kana"), (0x31F0, 0x31FF, "kana"), (0xFF66, 0xFF9F, "kana"),
    (0x1100, 0x11FF, "hangul"), (0x3130, 0x318F, "hangul"), (0xAC00, 0xD7AF, "hangul"),
    (0x3400, 0x4DBF, "han"), (0x4E00, 0x9FFF, "han"), (0xF900, 0xFAFF, "han"),
    (0x20000, 0x2FA1F, "han"),
    (0x0400, 0x04FF, "cyrillic"), (0x0500, 0x052F, "cyrillic"),
    (0x0370, 0x03FF, "greek"),
    (0x0590, 0x05FF, "hebrew"),
    (0x0600, 0x06FF, "arabic"), (0x0750, 0x077F, "arabic"), (0xFB50, 0xFDFF, "arabic"),
    (0x0900, 0x097F, "devanagari"),
    (0x0980, 0x09FF, "bengali"),
    (0x0A00, 0x0A7F, "gurmukhi"), (0x0A80, 0x0AFF, "gujarati"),
    (0x0B80, 0x0BFF, "tamil"), (0x0C00, 0x0C7F, "telugu"),
    (0x0C80, 0x0CFF, "kannada"), (0x0D00, 0x0D7F, "malayalam"),
    (0x0D80, 0x0DFF, "sinhala"),
    (0x0E00, 0x0E7F, "thai"), (0x0E80, 0x0EFF, "lao"),
    (0x0F00, 0x0FFF, "tibetan"), (0x1000, 0x109F, "myanmar"),
    (0x10A0, 0x10FF, "georgian"), (0x0530, 0x058F, "armenian"),
    (0x1200, 0x137F, "ethiopic"), (0x1780, 0x17FF, "khmer"),
]

# Scripts that map to exactly one language for practical purposes.
_SCRIPT_LANG = {
    "hangul": "ko", "greek": "el", "hebrew": "he", "thai": "th", "lao": "lo",
    "bengali": "bn", "gurmukhi": "pa", "gujarati": "gu", "tamil": "ta",
    "telugu": "te", "kannada": "kn", "malayalam": "ml", "sinhala": "si",
    "tibetan": "bo", "myanmar": "my", "georgian": "ka", "armenian": "hy",
    "ethiopic": "am", "khmer": "km",
}

_STOPWORDS = {
    "en": "the of and to in is that for with as on are by this be from or an which",
    "de": "der die und das ist nicht mit von den des auf für ein eine sich dem im",
    "fr": "le la les des et est une pour dans que qui sur du par pas avec sont au",
    "es": "el la de que los las en por con una para del es se como más pero al",
    "it": "il di che la per non una sono del della con gli le si nel alla è al",
    "pt": "de que não em uma para com os as por mais do da dos das se ao é",
    "nl": "de het een en van is dat op te zijn voor met niet aan er die als",
    "ru": "и в не на что с как по это из он к но для то от же о все",
    "pl": "i w nie na że się jest do to z jak co ale o od po tak dla",
    "tr": "ve bir bu da de için ile olarak daha gibi olan çok ne ama en",
    "id": "yang dan di ini dengan untuk dari dalam tidak itu pada akan adalah ke",
    "vi": "và của là có trong được cho không các những một với này người đã",
}
_STOPWORDS = {k: set(v.split()) for k, v in _STOPWORDS.items()}


def _script_of(ch: str) -> Optional[str]:
    cp = ord(ch)
    if cp < 0x0250:
        return "latin" if ch.isalpha() else None
    for lo, hi, name in _SCRIPT_RANGES:
        if lo <= cp <= hi:
            return name
    if ch.isalpha():
        return "latin" if 0x1E00 <= cp <= 0x1EFF else "other"
    return None


def script_counts(text: str) -> Counter:
    c = Counter()
    for ch in text:
        s = _script_of(ch)
        if s:
            c[s] += 1
    return c


def _clean(text: str) -> str:
    text = re.sub(r"https?://\S+|www\.\S+|\S+@\S+", " ", text)
    text = re.sub(r"\b(?:doi|issn|isbn)\b\S*", " ", text, flags=re.I)
    return text


def language_name(code: str) -> str:
    if not code:
        return "Unknown"
    base = code.split("-")[0]
    return LANGUAGE_NAMES.get(base, code)


def detect_language(text: str, short_ok: bool = False) -> Tuple[str, float]:
    """Return (ISO code, confidence 0..1). Code is "" when undetermined.

    ``short_ok`` lets Latin-script text shorter than the normal minimum be
    judged (used for titles); file names should leave it False.
    """
    if not text:
        return "", 0.0
    sample = _clean(text[:20000])
    counts = script_counts(sample)
    total = sum(counts.values())
    if total == 0:
        return "", 0.0

    han = counts["han"]
    kana = counts["kana"]
    hangul = counts["hangul"]
    latin = counts["latin"]
    latin_words = latin / 5.0   # rough: an average Latin word is ~5 letters

    cjk = han + kana + hangul
    # CJK characters each carry roughly one word of meaning, so compare
    # them against the Latin word count, not the letter count.
    if cjk >= 2 and cjk >= latin_words * 0.8:
        # Kana never appears in Chinese, so even one character is decisive
        # for short strings; long texts need a small share to rule out quotes.
        if kana >= 1 and kana >= cjk * 0.02:
            return "ja", min(1.0, 0.6 + cjk / 200.0)
        if hangul > han:
            return "ko", min(1.0, 0.6 + cjk / 200.0)
        if han:
            return "zh", min(1.0, 0.6 + cjk / 200.0)

    dominant, dom_n = counts.most_common(1)[0]
    if dominant in _SCRIPT_LANG and dom_n >= 2:
        return _SCRIPT_LANG[dominant], min(1.0, 0.7 + dom_n / 100.0)

    min_letters = 8 if short_ok else 40
    if dominant in ("arabic", "devanagari", "cyrillic", "latin"):
        if dom_n < min_letters and dominant == "latin":
            return "", 0.0
        if dom_n < 4:
            return "", 0.0
        code, conf = _statistical(sample)
        if code:
            return code, conf
        # Script-level fallbacks when statistics are unavailable/unsure.
        fallback = {"arabic": "ar", "devanagari": "hi", "cyrillic": "ru"}
        if dominant in fallback:
            return fallback[dominant], 0.5
    return "", 0.0


def _statistical(text: str) -> Tuple[str, float]:
    model = _fasttext()
    if model is not None:
        try:
            sample = " ".join(text[:6000].split())
            labels, probs = model.predict(sample, k=1)
            if labels:
                code = labels[0].replace("__label__", "")
                prob = float(probs[0])
                if code.startswith("zh"):
                    code = "zh"
                if prob >= 0.4:
                    return code, prob
        except Exception:
            pass
    if _HAVE_LANGDETECT:
        try:
            results = detect_langs(text[:5000])
        except LangDetectException:
            results = []
        if results:
            best = results[0]
            code = best.lang
            if code.startswith("zh"):
                code = "zh"
            if best.prob >= 0.5:
                return code, float(best.prob)
    words = re.findall(r"[^\W\d_]+", text.lower())
    if len(words) < 5:
        return "", 0.0
    votes = Counter()
    for w in words:
        for lang, sw in _STOPWORDS.items():
            if w in sw:
                votes[lang] += 1
    if not votes:
        return "", 0.0
    lang, n = votes.most_common(1)[0]
    share = n / float(sum(votes.values()))
    if n >= 3 and share >= 0.4:
        return lang, min(0.9, share)
    return "", 0.0


# Windows LCIDs found in CHM/Office files -> ISO codes
LCID_MAP = {
    0x0409: "en", 0x0809: "en", 0x0C09: "en", 0x1009: "en",
    0x0804: "zh", 0x0404: "zh", 0x0C04: "zh", 0x1004: "zh", 0x1404: "zh",
    0x0411: "ja", 0x0412: "ko", 0x0419: "ru", 0x0422: "uk", 0x0402: "bg",
    0x0407: "de", 0x0C07: "de", 0x0807: "de", 0x040C: "fr", 0x080C: "fr",
    0x0C0C: "fr", 0x040A: "es", 0x0C0A: "es", 0x080A: "es", 0x0410: "it",
    0x0416: "pt", 0x0816: "pt", 0x0413: "nl", 0x041D: "sv", 0x0406: "da",
    0x0414: "no", 0x040B: "fi", 0x0415: "pl", 0x0405: "cs", 0x040E: "hu",
    0x0418: "ro", 0x041F: "tr", 0x0408: "el", 0x0401: "ar", 0x040D: "he",
    0x041E: "th", 0x042A: "vi", 0x0421: "id", 0x0439: "hi", 0x0429: "fa",
}

# Windows code page for each language (for decoding legacy 8-bit strings)
LANG_CODEPAGE = {
    "zh": "gb18030", "ja": "cp932", "ko": "cp949", "ru": "cp1251",
    "uk": "cp1251", "bg": "cp1251", "el": "cp1253", "tr": "cp1254",
    "he": "cp1255", "ar": "cp1256", "th": "cp874", "vi": "cp1258",
    "pl": "cp1250", "cs": "cp1250", "hu": "cp1250", "ro": "cp1250",
}


def normalize_code(tag: str) -> str:
    """Turn tags like 'en-US', 'zh_CN', 'eng', 'chi', 'English' into 'en'/'zh'."""
    if not tag:
        return ""
    t = tag.strip().lower().replace("_", "-")
    three = {"eng": "en", "chi": "zh", "zho": "zh", "jpn": "ja", "kor": "ko",
             "rus": "ru", "ger": "de", "deu": "de", "fre": "fr", "fra": "fr",
             "spa": "es", "ita": "it", "por": "pt", "dut": "nl", "nld": "nl",
             "ara": "ar", "pol": "pl", "tur": "tr", "gre": "el", "ell": "el",
             "heb": "he", "hin": "hi", "tha": "th", "vie": "vi", "ukr": "uk",
             "swe": "sv", "cze": "cs", "ces": "cs", "hun": "hu", "per": "fa",
             "fas": "fa", "lat": "la"}
    base = t.split("-")[0]
    if base in three:
        return three[base]
    if len(base) == 2 and base.isalpha():
        return base
    for code, name in LANGUAGE_NAMES.items():
        if name.lower() == t:
            return code
    return ""
