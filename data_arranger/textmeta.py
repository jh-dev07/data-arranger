"""Heuristic bibliographic metadata extraction from plain text.

These rules target the first pages of papers, theses, books, patents and
standards in English and Chinese. They only fill fields that are still empty,
so embedded metadata (PDF info, XMP, Office properties...) always wins.
"""
import os
import re
from typing import Dict, List, Optional

from . import identifiers as ids
from .models import FileRecord

_HEADINGS_END = (
    r"(?:\n\s*(?:key\s*words?|index\s+terms|关键词|关键字|【关键词】|"
    r"1\.?\s+introduction|I\.\s+INTRODUCTION|introduction|引言|前言|"
    r"0\s+引言|1\s+引言|一、|中图分类号|CLC|文献标识码|ABSTRACT)\b)"
)

_ABSTRACT_RE = re.compile(
    r"(?:^|\n)\s*(?:abstract|summary|摘\s*要|内容提要|【摘要】|\[摘要\])\s*[:：.\-—]?\s*"
    r"(.{40,4000}?)" + "(?=" + _HEADINGS_END + r"|\Z)",
    re.I | re.S)

_KEYWORDS_RE = re.compile(
    r"(?:key\s*words?|index\s+terms|关\s*键\s*词|关键字|【关键词】)\s*[:：.\-—]?\s*"
    r"([^\n]{2,400}(?:\n(?![A-Z0-9一-龥]{1,20}[:：]|\s*\d+(?:\.\d+)*\.?\s|\s*(?:introduction|引言|前言|中图分类号|CLC|DOI|abstract)\b)[^\n]{0,200})?)",
    re.I)

_VOL_ISSUE_RES = [
    re.compile(r"\bVol(?:ume)?\.?\s*(\d{1,4})\s*[,，]?\s*(?:No|Issue|Iss|Number|Nr)\.?\s*(\d{1,4}(?:\s*[-/]\s*\d{1,4})?)", re.I),
    re.compile(r"第\s*(\d{1,4})\s*卷\s*第?\s*(\d{1,3})\s*期"),
    re.compile(r"\b(\d{1,4})\s*\((\d{1,3}(?:-\d{1,3})?)\)\s*[:：]\s*\d+"),  # 12(3): 45-67
]
_VOL_ONLY_RE = re.compile(r"\bVol(?:ume)?\.?\s*(\d{1,4})\b|第\s*(\d{1,4})\s*卷", re.I)
_ISSUE_CN_RE = re.compile(r"((?:19|20)\d\d)\s*年\s*第?\s*(\d{1,3})\s*期")

_PUBLISHERS = [
    "Elsevier", "Springer Nature", "Springer", "Wiley-Blackwell", "Wiley",
    "IEEE", "ACM", "Taylor & Francis", "Routledge", "SAGE Publications", "SAGE",
    "Oxford University Press", "Cambridge University Press", "MIT Press",
    "Princeton University Press", "Yale University Press", "Pearson",
    "McGraw-Hill", "O'Reilly", "Packt", "Apress", "Manning", "Addison-Wesley",
    "Prentice Hall", "CRC Press", "World Scientific", "De Gruyter", "Hindawi",
    "MDPI", "Frontiers Media", "PLOS", "Nature Publishing Group",
    "American Chemical Society", "American Physical Society", "IOP Publishing",
    "Royal Society of Chemistry", "BMJ", "Lippincott", "Thieme", "Karger",
    "Emerald", "Brill", "Palgrave Macmillan", "Macmillan", "Penguin",
    "HarperCollins", "Random House", "Wolters Kluwer", "John Benjamins",
]
_PUBLISHER_CN_RE = re.compile(r"([一-龥]{2,20}(?:出版社|出版集团|书局|印书馆|出版公司))")
_PUBLISHER_EN_RE = re.compile(
    r"(?:Published\s+by|Publisher\s*[:：])\s*([A-Z][\w&.,' -]{2,80}?)(?:\n|,\s*(?:Inc|Ltd|LLC)|\.|$)",
    re.M)

_JOURNAL_RES = [
    re.compile(r"((?:The\s+)?(?:International\s+)?Journal\s+of\s+[A-Z][\w&,:' -]{3,90})"),
    re.compile(r"((?:Annals|Proceedings|Transactions|Letters|Reviews?|Bulletin|Archives)\s+(?:of|on)\s+(?:the\s+)?[A-Z][\w&,:' -]{3,80})"),
    re.compile(r"(IEEE\s+Transactions\s+on\s+[A-Z][\w&,:' -]{3,80})"),
    re.compile(r"([一-龥]{2,24}(?:学报|杂志|期刊|研究|评论|通讯|论坛|科学|技术|月刊|季刊)(?:\s*[(（][一-龥]{2,10}版[)）])?)\s*(?:\d{4}\s*年|第\s*\d+\s*卷|\d{4}[,，]|ISSN|CN)"),
]

_CONFERENCE_RE = re.compile(
    r"((?:Proceedings\s+of\s+(?:the\s+)?)?(?:\d{1,4}(?:st|nd|rd|th)\s+)?"
    r"(?:Annual\s+|International\s+|IEEE\s+|ACM\s+)*"
    r"(?:Conference|Symposium|Workshop|Congress|Colloquium)\s+on\s+[A-Z][\w&,:' -]{3,90})")
_CONFERENCE_CN_RE = re.compile(r"([一-龥]{2,40}(?:会议|研讨会|论坛|年会)(?:论文集)?)")

_DATE_RES = [
    re.compile(r"(?:Published(?:\s+online)?|Publication\s+date|出版日期|发表日期|发布日期|公开日|公告日)\s*[:：]?\s*"
               r"(\d{4}[-/.年]\s?\d{1,2}(?:[-/.月]\s?\d{1,2}日?)?|\d{1,2}\s+[A-Z][a-z]+\s+\d{4}|[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})", re.I),
    re.compile(r"(?:©|\(c\)|Copyright)\s*((?:19|20)\d\d)", re.I),
    re.compile(r"((?:19|20)\d\d)\s*年\s*\d{1,2}\s*月"),
]

_DEGREE_RES = [
    (re.compile(r"博士学位论文|博士论文|Doctor\s+of\s+Philosophy|Ph\.?\s?D\.?\s+(?:thesis|dissertation)|doctoral\s+(?:thesis|dissertation)", re.I), "Doctoral"),
    (re.compile(r"硕士学位论文|硕士论文|专业学位|Master(?:'s)?\s+(?:of\s+\w+|thesis|degree)|M\.?Sc\.?\s+thesis", re.I), "Master"),
    (re.compile(r"学士学位论文|本科毕业论文|毕业设计|Bachelor(?:'s)?\s+(?:of\s+\w+|thesis)|undergraduate\s+thesis", re.I), "Bachelor"),
]
_INSTITUTION_RES = [
    re.compile(r"([一-龥]{2,20}(?:大学|学院|研究院|研究所|科学院))"),
    re.compile(r"((?:University|Institute|College)\s+of\s+[A-Z][\w&' -]{2,60}|[A-Z][\w&' -]{2,50}\s+(?:University|Institute of Technology))"),
]


def _clean_field(s: str, limit: int = 300) -> str:
    s = re.sub(r"\s+", " ", s or "").strip(" \t;:：,，.-—")
    return s[:limit]


def extract_abstract(text: str) -> str:
    m = _ABSTRACT_RE.search(text)
    if not m:
        return ""
    body = m.group(1)
    # Join hyphenated line breaks, then flatten whitespace.
    body = re.sub(r"-\n(?=[a-z])", "", body)
    return _clean_field(body, 3000)


def extract_keywords(text: str) -> List[str]:
    m = _KEYWORDS_RE.search(text)
    if not m:
        return []
    raw = m.group(1).replace("\n", " ")
    raw = re.split(r"\b(?:中图分类号|CLC|文献标识码|Document code|DOI|1\s+Introduction|Introduction)\b", raw)[0]
    parts = re.split(r"[;；,，、·•]|\s{2,}", raw)
    out = []
    for p in parts:
        p = _clean_field(p, 80)
        if 1 < len(p) <= 60 and p.lower() not in (x.lower() for x in out):
            out.append(p)
    return out[:15]


def extract_volume_issue(text: str) -> Dict[str, str]:
    out = {}
    for rx in _VOL_ISSUE_RES:
        m = rx.search(text)
        if m:
            out["volume"], out["issue"] = m.group(1), re.sub(r"\s", "", m.group(2))
            break
    if "volume" not in out:
        m = _VOL_ONLY_RE.search(text)
        if m:
            out["volume"] = m.group(1) or m.group(2)
    m = _ISSUE_CN_RE.search(text)
    if m:
        out.setdefault("issue", m.group(2))
        out.setdefault("year", m.group(1))
    return out


def extract_publisher(text: str) -> str:
    m = _PUBLISHER_CN_RE.search(text)
    if m:
        return m.group(1)
    m = _PUBLISHER_EN_RE.search(text)
    if m:
        return _clean_field(m.group(1), 100)
    for p in _PUBLISHERS:
        if re.search(r"\b" + re.escape(p) + r"\b", text):
            return p
    return ""


def extract_journal(text: str) -> str:
    for rx in _JOURNAL_RES:
        m = rx.search(text)
        if m:
            j = _clean_field(m.group(1), 120)
            j = re.split(r"\s{2,}|\s(?:Vol|Volume|No|Issue)\b", j)[0]
            return j.strip(" ,:")
    return ""


def extract_conference(text: str) -> str:
    m = _CONFERENCE_RE.search(text)
    if m:
        return _clean_field(m.group(1), 150)
    m = _CONFERENCE_CN_RE.search(text)
    if m:
        return m.group(1)
    return ""


def extract_date(text: str) -> str:
    for rx in _DATE_RES:
        m = rx.search(text)
        if m:
            return _clean_field(m.group(1), 40)
    return ""


def extract_degree(text: str) -> str:
    for rx, label in _DEGREE_RES:
        if rx.search(text):
            return label
    return ""


def extract_institution(text: str) -> str:
    for rx in _INSTITUTION_RES:
        m = rx.search(text)
        if m:
            return _clean_field(m.group(1), 120)
    return ""


# --- Title / author guesses -------------------------------------------------

_BAD_TITLE_RE = re.compile(
    r"^(?:microsoft\s+(?:word|powerpoint)\s*-|untitled|unknown|title|无标题|"
    r"document\d*|doc\d+|slide\s*\d+|presentation\d*|\s*$|.*\.(?:docx?|pdf|pptx?|tex|dvi|qxd|indd)$)",
    re.I)


# Header lines that are labels, not titles.
_BOILERPLATE_RE = re.compile(
    r"^(?:中华人民共和国(?:国家标准|[一-龥]{2,6}行业标准)|[一-龥]{0,8}(?:国家|行业|团体|地方|企业)标准|"
    r"(?:博士|硕士|学士)?学位论文|毕业(?:论文|设计)|发明专利(?:申请)?|实用新型专利|"
    r"(?:\(\d+\))?中华人民共和国国家知识产权局|国家知识产权局|原\s*创\s*性\s*声\s*明|"
    r"international\s+standard|technical\s+report|research\s+article|original\s+article|"
    r"review\s+article|article|letter|thesis|dissertation|united\s+states\s+patent)$", re.I)


def plausible_title(t: str) -> bool:
    if not t:
        return False
    t = t.strip()
    if len(t) < 4 or len(t) > 400:
        return False
    if _BAD_TITLE_RE.match(t) or _BOILERPLATE_RE.match(t):
        return False
    letters = sum(ch.isalpha() for ch in t)
    if letters < len(t) * 0.4:
        return False
    # Hex/GUID-like garbage
    if re.fullmatch(r"[0-9a-f\-_ ]{8,}", t, re.I):
        return False
    return True


_NAME_EN = r"[A-Z][a-zA-Z'\-]+(?:\s+[A-Z]\.?)*(?:\s+[A-Z][a-zA-Z'\-]+){1,2}"
_AUTHOR_LINE_EN = re.compile(
    r"^\s*(" + _NAME_EN + r")(?:[\d,*†‡§¶\s]*(?:,|and|&)\s*(" + _NAME_EN + r"))*[\d,*†‡§¶\s]*$")


def split_authors(line: str) -> List[str]:
    line = re.sub(r"[\d*†‡§¶✉]+", " ", line)
    parts = re.split(r"\s*(?:,|;|，|、|；|\band\b|&|\s{2,})\s*", line)
    out = []
    for p in parts:
        p = " ".join(p.split())
        if 1 < len(p) < 60 and p not in out:
            out.append(p)
    return out


# Common Chinese surnames (single and compound); used to tell a line of
# personal names from an ordinary short phrase.
_CN_SURNAMES = set(
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈"
    "姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤"
    "常温康施文牛樊葛邢安齐易乔伍庞颜倪庄聂章鲁岳翟殷詹申欧耿关兰焦俞左柳甘祝包宁尚符舒阮柯纪梅童凌毕单季"
    "裴霍涂成苗谷盛曲翁冉骆蓝路游辛靳管柴蒙鲍华喻祁蒲房滕屈饶解牟艾尤阳时穆农司卓古吉缪简车项连芦麦褚娄窦"
    "戚岑景党宫费卜冷晏席卫米柏宗瞿桂全佟应臧闵苟邬边卞姬师和仇栾隋商刁沙荣巫寇桑郎甄丛仲虞敖巩明佘池查麻"
    "苑迟邝官封谈匡鞠惠荆乐冀郁胥南班储原栗燕楚鄢劳谌奚皮粟冼蔺楼盘满闻位厉伊仝区郜海阚花权强帅屠豆朴盖练"
    "廉禹井祖漆巴丰支卿国狄平计索宣晋相初门云容敬来扈晁芮都普阙浦戈伏鹿薄邸雍辜羊阿乌母裘亓修邰赫杭况那宿"
    "鲜印逯隆茹诸战慕危玉银亢嵇公哈湛宾戎勾茅利於呼居揭干但尉冶斯元束檀衣信展阴昝智幸奉植衡富尧闭由"
)
_CN_COMPOUND = ("欧阳", "司马", "诸葛", "上官", "东方", "慕容", "令狐", "皇甫", "尉迟", "公孙", "长孙", "宇文", "司徒", "夏侯", "轩辕", "端木", "独孤", "南宫", "西门")


def _is_cn_name(tok: str) -> bool:
    tok = tok.replace("·", "")
    if not 2 <= len(tok) <= 4:
        return False
    return tok[0] in _CN_SURNAMES or tok[:2] in _CN_COMPOUND


def guess_authors(lines: List[str]) -> List[str]:
    """Given the lines immediately after the title, find an author line."""
    for line in lines[:4]:
        s = line.strip()
        if not s or len(s) > 200:
            continue
        # Chinese author lines: 2-4 Han chars per name, separated by spaces/commas
        if re.search(r"[一-龥]", s) and not re.search(r"(大学|学院|研究|摘要|关键|学报|公司|标准|专利)", s):
            tokens = [t for t in re.split(r"[\s,，、;；\d*()（）]+", s) if t]
            if tokens and len(tokens) <= 15 and all(_is_cn_name(t) for t in tokens):
                return tokens
        if _AUTHOR_LINE_EN.match(s) and not re.search(
                r"\b(University|Institute|Department|Abstract|Journal|School|Laboratory)\b", s, re.I):
            names = split_authors(s)
            if names:
                return names[:15]
    return []


def title_from_font_spans(spans) -> str:
    """spans: list of (text, font_size, order). Pick the biggest-font block near
    the top of the first page as the title."""
    spans = [(t.strip(), size, order) for t, size, order in spans if t and t.strip()]
    if not spans:
        return ""
    sizes = sorted({round(s, 1) for _, s, _ in spans}, reverse=True)
    for size in sizes[:3]:
        parts = [t for t, s, _ in sorted(spans, key=lambda x: x[2]) if round(s, 1) == size]
        cand = _clean_field(" ".join(parts), 400)
        # Very large but very short text is often a journal logo or page number.
        if plausible_title(cand) and len(cand) >= 8:
            return cand
    return ""


def parse_filename(path: str) -> Dict[str, object]:
    """Extract title/author hints from a file name.

    Handles the CNKI download convention "Title_Author1_Author2.caj", and
    "Author - Title (2019)" style names used for books.
    """
    base = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
    base = os.path.splitext(base)[0]
    base = re.sub(r"\s*\(\d\)$|\s*-\s*副本$|\s*-\s*copy$", "", base, flags=re.I)
    out = {}
    m = re.search(r"[(\[（]((?:19|20)\d\d)[)\]）]", base)
    if m:
        out["year"] = m.group(1)
        base_wo = (base[:m.start()] + base[m.end():]).strip(" -_")
    else:
        base_wo = base
    if "_" in base_wo and re.search(r"[一-龥]", base_wo):
        parts = [p for p in base_wo.split("_") if p]
        if len(parts) >= 2 and all(re.fullmatch(r"[一-龥·]{2,5}", p) for p in parts[1:]):
            out["title"] = parts[0]
            out["authors"] = parts[1:]
            return out
    m = re.fullmatch(r"(.{2,80}?)\s+-\s+(.{4,})", base_wo)
    if m and len(m.group(1).split()) <= 6:
        out["authors"] = split_authors(m.group(1))
        out["title"] = m.group(2).strip()
        return out
    clean = re.sub(r"[_]+", " ", base_wo).strip()
    if plausible_title(clean) and not re.fullmatch(r"[\w\-]{1,12}", clean):
        out["title"] = clean
    return out


def fill_from_text(rec: FileRecord, text: str) -> None:
    """Fill empty fields of rec using rules over the extracted text."""
    if not text:
        return
    head = text[:30000]
    src = "text"
    rec.set_if_empty("abstract", extract_abstract(head), src)
    rec.set_if_empty("keywords", extract_keywords(head), src)

    isbns = ids.find_isbns(head)
    if isbns:
        rec.set_if_empty("isbn", isbns[0], src)
        if len(isbns) > 1:
            rec.extra.setdefault("other_isbns", ", ".join(isbns[1:]))
    issns = ids.find_issns(head)
    if issns:
        rec.set_if_empty("issn", issns[0], src)
    rec.set_if_empty("doi", ids.find_doi(head) or "", src)
    cn = ids.find_cn_serial(head)
    if cn:
        rec.extra.setdefault("cn_serial", cn)

    vi = extract_volume_issue(head[:8000])
    rec.set_if_empty("volume", vi.get("volume", ""), src)
    rec.set_if_empty("issue", vi.get("issue", ""), src)
    rec.set_if_empty("journal", extract_journal(head[:8000]), src)
    rec.set_if_empty("conference", extract_conference(head[:8000]), src)
    rec.set_if_empty("publisher", extract_publisher(head), src)
    date = extract_date(head) or vi.get("year", "")
    rec.set_if_empty("publish_date", date, src)

    rec.set_if_empty("patent_number", ids.find_patent_number(head[:5000]) or "", src)
    rec.set_if_empty("standard_number", ids.find_standard_number(head[:3000]) or "", src)

    degree = extract_degree(head[:6000])
    if degree:
        rec.set_if_empty("degree", degree, src)
        rec.set_if_empty("institution", extract_institution(head[:6000]), src)

    if not rec.title or not rec.authors:
        lines = [l.strip() for l in head[:3000].splitlines() if l.strip()]
        if not rec.title:
            for i, line in enumerate(lines[:15]):
                if plausible_title(line) and len(line) >= 10 and not re.search(
                        r"(journal|vol\.|volume|issn|doi|http|学报|第\s*\d+\s*卷|©|copyright)",
                        line, re.I):
                    rec.set_if_empty("title", line, "text")
                    if not rec.authors:
                        rec.set_if_empty("authors", guess_authors(lines[i + 1:i + 5]), "text")
                    break
        elif not rec.authors:
            try:
                idx = next(i for i, l in enumerate(lines[:20]) if rec.title[:20] in l)
            except StopIteration:
                idx = -1
            if idx >= 0:
                rec.set_if_empty("authors", guess_authors(lines[idx + 1:idx + 5]), "text")
