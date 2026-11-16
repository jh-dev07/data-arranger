"""Bibliographic identifiers: ISBN, ISSN, DOI, CN serial numbers, patent and
standard numbers. Checksums are validated so random digit runs don't match."""
import re
from typing import List, Optional

_DASHES = "‐‑‒–—−－"


def _norm(text: str) -> str:
    for d in _DASHES:
        text = text.replace(d, "-")
    return text.replace("：", ":")


def isbn10_ok(s: str) -> bool:
    if not re.fullmatch(r"\d{9}[\dX]", s):
        return False
    total = sum((10 - i) * int(c) for i, c in enumerate(s[:9]))
    total += 10 if s[9] == "X" else int(s[9])
    return total % 11 == 0


def isbn13_ok(s: str) -> bool:
    if not re.fullmatch(r"97[89]\d{10}", s):
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s[:12]))
    return (10 - total % 10) % 10 == int(s[12])


def isbn10_to_13(s: str) -> str:
    core = "978" + s[:9]
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core))
    return core + str((10 - total % 10) % 10)


_ISBN_RE = re.compile(
    r"ISBN(?:-1[03])?[\s:：]*((?:97[89][\s-]?)?(?:\d[\s-]?){9}[\dXx])\b", re.I)
_ISBN_BARE_RE = re.compile(r"\b(97[89](?:[\s-]?\d){10})\b")


def find_isbns(text: str) -> List[str]:
    text = _norm(text)
    found = []
    for m in list(_ISBN_RE.finditer(text)) + list(_ISBN_BARE_RE.finditer(text)):
        raw = re.sub(r"[\s-]", "", m.group(1)).upper()
        if len(raw) == 10 and isbn10_ok(raw):
            raw = isbn10_to_13(raw)
        if len(raw) == 13 and isbn13_ok(raw) and raw not in found:
            found.append(raw)
    return found


def issn_ok(s: str) -> bool:
    d = s.replace("-", "")
    if not re.fullmatch(r"\d{7}[\dX]", d):
        return False
    total = sum((8 - i) * int(c) for i, c in enumerate(d[:7]))
    check = (11 - total % 11) % 11
    return d[7] == ("X" if check == 10 else str(check))


_ISSN_RE = re.compile(r"\b(?:e-?|p-?|print\s*|online\s*)?ISSN[\s:：]*(\d{4}-?\d{3}[\dXx])", re.I)


def find_issns(text: str) -> List[str]:
    out = []
    for m in _ISSN_RE.finditer(_norm(text)):
        s = m.group(1).upper()
        if "-" not in s:
            s = s[:4] + "-" + s[4:]
        if issn_ok(s) and s not in out:
            out.append(s)
    return out


_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>，。；]+)", re.I)


def find_doi(text: str) -> Optional[str]:
    m = _DOI_RE.search(_norm(text))
    if not m:
        return None
    doi = m.group(1).rstrip(".,;:)]}'")
    # A trailing ')' is legitimate only when the DOI has an opening '('.
    return doi


# Chinese domestic serial number, e.g. "CN 11-2223/N"
_CN_RE = re.compile(r"\bCN\s?(\d{2}-\d{4}/[A-Z]{1,2})\b")


def find_cn_serial(text: str) -> Optional[str]:
    m = _CN_RE.search(_norm(text))
    return m.group(1) if m else None


_PATENT_RES = [
    re.compile(r"\b(CN\s?\d{9}(?:\.\d)?\s?[ABUCS]\d?)\b"),
    re.compile(r"\b(CN\s?\d{12}(?:\.\d)?)\b"),
    re.compile(r"\b(US\s?(?:\d{1,2},?\d{3},?\d{3}|\d{4}/?\d{7})\s?[AB]\d)\b"),
    re.compile(r"\b(EP\s?\d{7}\s?[AB]\d)\b"),
    re.compile(r"\b(WO\s?\d{4}/?\d{6}\s?A\d)\b"),
    re.compile(r"\b(JP\s?\d{4}-?\d{6}\s?A)\b"),
    re.compile(r"\b(KR\s?10-?\d{7}\s?B\d)\b"),
    re.compile(r"\b(DE\s?\d{2,3}\s?\d{2}\s?\d{3}\s?[AB]\d)\b"),
]


def find_patent_number(text: str) -> Optional[str]:
    text = _norm(text)
    for rx in _PATENT_RES:
        m = rx.search(text)
        if m:
            return " ".join(m.group(1).split())
    return None


_STD_RE = re.compile(
    r"\b((?:GB(?:/[TZ])?|GJB|JB(?:/T)?|HG(?:/T)?|YY(?:/T)?|DL(?:/T)?|SJ(?:/T)?|"
    r"QB(?:/T)?|JGJ|CJJ|YD(?:/T)?|NB(?:/T)?|SH(?:/T)?|HJ|DB\d{2}(?:/T)?|"
    r"T/[A-Z]{2,8}|ISO(?:/IEC)?(?:/TS|/TR)?|IEC|EN|DIN(?:\s?EN)?|BS(?:\s?EN)?|"
    r"ANSI(?:/[A-Z]+)?|ASTM|IEEE\s?Std|JIS|NF|GOST|ITU-T\s?[A-Z]|ASME|SAE|UL)"
    r"\s?[A-Z]?\s?\d+(?:[.\-]\d+)*(?:[:\-]\s?(?:19|20)\d\d)?)")


def find_standard_number(text: str) -> Optional[str]:
    m = _STD_RE.search(_norm(text))
    if not m:
        return None
    s = " ".join(m.group(1).split())
    # Avoid matching things like "EN 2" in ordinary prose: require 3+ digits
    # or a year suffix for the ambiguous short prefixes.
    if re.match(r"(EN|NF|UL|HJ|SAE)\s?\d{1,2}$", s):
        return None
    return s
