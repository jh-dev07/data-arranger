"""Rule-based document-type classification.

Each rule adds weight to a document type and records a human-readable
reason, so the user can see *why* a file was put where it was. Embedded
identifiers (ISBN, ISSN, patent/standard numbers) are the strongest signals;
phrases in the first pages and file-format priors add to them.
"""
import re
from collections import defaultdict
from typing import List, Tuple

from .models import (BOOK, CONFERENCE, DISSERTATION, JOURNAL, NEWSPAPER,
                     OTHER_DOC, PATENT, REPORT, STANDARD, FileRecord)

# (doc type, weight, regex, reason)
_PHRASES = [
    (DISSERTATION, 4, r"学位论文|博士论文|硕士论文|毕业论文|学位申请|论文作者签名|学位授予单位|答辩委员会|独创性声明|"
                      r"submitted\s+in\s+partial\s+fulfil+ment|(?:doctoral|master'?s?|ph\.?\s?d\.?)\s+(?:thesis|dissertation)|"
                      r"\bdissertation\s+(?:submitted|presented)|thesis\s+(?:submitted|presented)\s+(?:to|for)|"
                      r"\bthesis\s+(?:advisor|supervisor|committee)", "thesis wording"),
    (DISSERTATION, 2, r"指导教师|导\s*师\s*[:：]|学科专业|研究方向|supervisor\s*[:：]|advisor\s*[:：]|"
                      r"in\s+candidacy\s+for\s+the\s+degree", "supervisor / degree fields"),
    (PATENT, 5, r"发明专利|实用新型|外观设计专利|专利号|申请号|公开号|授权公告号|申请公布号|"
                r"United\s+States\s+Patent|Patent\s+Application\s+Publication|European\s+Patent|"
                r"\(\s*(?:21|22|43|51|54|57|71|72|73)\s*\)\s|Int\.\s*Cl\.|"
                r"(?:Appl|Application)\.?\s*No\.?\s*[:：]?\s*\d", "patent fields"),
    (STANDARD, 5, r"中华人民共和国国家标准|行业标准|团体标准|地方标准|企业标准|"
                  r"发布\s*\d{4}\s*-\s*\d{2}\s*-\s*\d{2}\s*实施|\bICS\s+\d{2}\.\d{3}|"
                  r"International\s+Standard|American\s+National\s+Standard|British\s+Standard|"
                  r"Standard\s+(?:Specification|Test\s+Method|Practice)\s+for", "standard wording"),
    (CONFERENCE, 4, r"proceedings\s+of|in\s+proceedings|conference\s+on|symposium\s+on|workshop\s+on|"
                    r"\bcongress\s+on|会议论文|学术会议|研讨会|年会论文|会议录", "conference wording"),
    (JOURNAL, 3, r"\bvol(?:ume)?\.?\s*\d+\s*,?\s*(?:no|issue)\.?\s*\d+|第\s*\d+\s*卷\s*第?\s*\d+\s*期|"
                 r"\d{4}\s*年\s*第\s*\d+\s*期|"
                 r"\breceived\s*[:：]?\s*\d|\baccepted\s*[:：]?\s*\d|收稿日期|修回日期|文章编号|文献标识码|中图分类号|"
                 r"journal\s+homepage|available\s+online\s+at|article\s+history", "journal article fields"),
    (JOURNAL, 2, r"journal\s+of|学报|杂志|期刊|letters\b|transactions\s+on", "journal name"),
    (BOOK, 3, r"图书在版编目|CIP\s*数据|版权所有|印\s*张|开\s*本|定\s*价|字\s*数|第\s*\d+\s*版|第\s*\d+\s*次印刷|"
              r"all\s+rights\s+reserved|library\s+of\s+congress\s+cataloging|printed\s+in\s+|first\s+(?:published|edition)|"
              r"\b(?:\d+(?:st|nd|rd|th)|second|third)\s+edition", "book imprint wording"),
    (BOOK, 2, r"\btable\s+of\s+contents\b|^\s*contents\s*$|^\s*目\s*录\s*$|\bchapter\s+\d+|第\s*[一二三四五六七八九十\d]+\s*章|"
              r"\bpreface\b|序\s*言|前\s*言|\bforeword\b", "chapters / preface"),
    (REPORT, 3, r"technical\s+report|research\s+report|white\s+paper|annual\s+report|working\s+paper|"
                r"研究报告|调研报告|年度报告|白皮书|可行性研究报告|工作报告", "report wording"),
    (NEWSPAPER, 4, r"[一-龥]{2,8}报\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*第\s*\d+\s*版|"
                   r"\(\s*\d{4}-\d{2}-\d{2}\s*\)\s*第\s*\d+\s*版", "newspaper dateline"),
]
_PHRASES = [(t, w, re.compile(rx, re.I | re.M), why) for t, w, rx, why in _PHRASES]

# File-format priors (CNKI formats map to databases with known content).
_EXT_PRIOR = {
    "nh": (DISSERTATION, 4, "NH is CNKI's thesis format"),
    "kdh": (JOURNAL, 1, "KDH is mostly used for CNKI journals"),
    "pdg": (BOOK, 5, "PDG is the Chaoxing book page format"),
    "epub": (BOOK, 4, "e-book format"),
    "mobi": (BOOK, 4, "e-book format"), "azw": (BOOK, 4, "e-book format"),
    "azw3": (BOOK, 4, "e-book format"), "fb2": (BOOK, 4, "e-book format"),
    "djvu": (BOOK, 2, "DjVu is mostly used for scanned books"),
    "djv": (BOOK, 2, "DjVu is mostly used for scanned books"),
    "chm": (BOOK, 2, "CHM is mostly used for e-books/manuals"),
    "uvz": (BOOK, 3, "e-book format"),
}

_FILENAME_HINTS = [
    (DISSERTATION, r"学位论文|博士|硕士|thesis|dissertation"),
    (PATENT, r"专利|patent|^CN\d{9}|^US\d{7}"),
    (STANDARD, r"^(?:GB|GB/?T|ISO|IEC|ASTM|DIN|EN|BS|JIS|IEEE\s?Std)[\s_-]?\d|标准"),
    (CONFERENCE, r"proceedings|conference|symposium|会议"),
    (REPORT, r"report|报告|白皮书"),
]
_FILENAME_HINTS = [(t, re.compile(rx, re.I)) for t, rx in _FILENAME_HINTS]


def classify(rec: FileRecord, text: str) -> Tuple[str, float, List[str]]:
    scores = defaultdict(float)
    reasons = defaultdict(list)

    def add(doc_type, weight, why):
        scores[doc_type] += weight
        reasons[doc_type].append(why)

    if rec.patent_number:
        add(PATENT, 6, "patent number " + rec.patent_number)
    if rec.standard_number and re.match(r"(GB|ISO|IEC|ASTM|DIN|BS|JIS|ANSI|IEEE|T/)", rec.standard_number):
        # Standards cite other standards, so a number alone is only medium weight
        add(STANDARD, 3, "standard number " + rec.standard_number)
    if rec.isbn:
        add(BOOK, 5, "has ISBN")
    if rec.issn or rec.extra.get("cn_serial"):
        add(JOURNAL, 3, "has ISSN/CN serial number")
    if rec.journal:
        add(JOURNAL, 2, "journal name found")
    if rec.doi and (rec.journal or rec.issn):
        add(JOURNAL, 2, "DOI in a journal")
    if rec.volume and rec.issue:
        add(JOURNAL, 2, "volume and issue found")
    if rec.conference:
        add(CONFERENCE, 3, "conference name found")
    if rec.degree:
        add(DISSERTATION, 3, rec.degree + " degree mentioned")

    prior = _EXT_PRIOR.get(rec.ext)
    if prior:
        add(prior[0], prior[1], prior[2])

    head = (text or "")[:15000]
    if head:
        first = head[:4000]
        for doc_type, weight, rx, why in _PHRASES:
            if rx.search(first):
                add(doc_type, weight, why)
            elif rx.search(head):
                add(doc_type, weight * 0.5, why + " (later pages)")

    name = rec.name
    for doc_type, rx in _FILENAME_HINTS:
        if rx.search(name):
            add(doc_type, 2, "file name")

    if rec.pages:
        if rec.pages >= 150:
            add(BOOK, 2 if not scores.get(DISSERTATION) else 0.5, "%d pages" % rec.pages)
        elif rec.pages <= 30 and scores.get(BOOK, 0) < 5:
            add(JOURNAL, 0.5, "short document")
        if 40 <= rec.pages <= 400 and scores.get(DISSERTATION):
            add(DISSERTATION, 1, "thesis-length")

    # Online lookups (Crossref etc.) put an authoritative type in extra.
    authoritative = rec.extra.get("type_from_lookup")
    if authoritative:
        add(authoritative, 10, "online catalog record")

    if not scores:
        return OTHER_DOC, 0.0, []
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score < 2.5:
        return OTHER_DOC, round(best_score / 10.0, 2), reasons[best]
    confidence = min(1.0, best_score / 10.0) * (1.0 - 0.5 * runner_up / best_score)
    return best, round(confidence, 2), reasons[best]
