"""End-to-end self-test: `DataArranger.exe --selftest result.json`.

Generates sample documents (text PDF, scanned/image-only Chinese PDF,
DOCX), analyses them through the same isolated worker the app uses (OCR +
local ML model), arranges them into a temp folder, then undoes it.
Writes a JSON report and exits 0 only if every check passed.
"""
import json
import os
import shutil
import tempfile
import time
import traceback
import zipfile

EN_PAPER = [
    (22, "Deep Learning Methods for Classifying Hearing Loss"),
    (12, "John Smith, Maria Garcia and Wei Zhang"),
    (10, "Department of Audiology, University of Toronto, Canada"),
    (10, "Journal of Hearing Research, Vol. 12, No. 3, 2019, pp. 45-61"),
    (10, "ISSN 0378-5955   https://doi.org/10.1016/j.heares.2019.107800"),
    (10, "Abstract: We propose a convolutional neural network that classifies the type and"),
    (10, "degree of hearing loss from pure-tone audiograms. On three clinical datasets the"),
    (10, "model reaches an accuracy of 94 percent, outperforming rule-based methods."),
    (10, "Keywords: deep learning; audiology; hearing loss; classification"),
    (12, "1. Introduction"),
    (10, "Hearing loss affects more than 400 million people worldwide and its diagnosis"),
    (10, "requires trained specialists who interpret audiograms by hand."),
]

ZH_THESIS = [
    (12, "分类号：TP391          密级：公开"),
    (12, "硕士学位论文"),
    (22, "基于深度学习的中文文本分类方法研究"),
    (14, "研究生：王小明"),
    (14, "指导教师：李建国 教授"),
    (14, "学科专业：计算机应用技术"),
    (14, "清华大学 计算机科学与技术系"),
    (14, "2021年6月"),
    (12, "摘要：随着互联网的快速发展，文本数据呈现爆炸式增长，如何对海量中文文本进行"),
    (12, "高效准确的分类成为研究热点。本文提出了一种基于卷积神经网络的文本分类方法。"),
    (12, "关键词：深度学习；文本分类；卷积神经网络"),
]


def _make_pdf(path, lines, scanned=False):
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 80
    for size, text in lines:
        font = "china-s" if any("一" <= c <= "鿿" for c in text) else "helv"
        page.insert_text((60, y), text, fontsize=size, fontname=font)
        y += size * 1.9
    if scanned:
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        img = fitz.open()
        p2 = img.new_page(width=595, height=842)
        p2.insert_image(p2.rect, stream=pix.tobytes("png"))
        img.save(path)
        img.close()
    else:
        doc.save(path)
    doc.close()


def _make_docx(path):
    body = ("Research report on regional water quality\n"
            "This technical report presents the annual results of water quality monitoring "
            "in the river basin and discusses trends of nitrate and phosphate levels.")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("docProps/core.xml", (
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Annual Water Quality Report 2022</dc:title>'
            '<dc:creator>Anna Berg</dc:creator></cp:coreProperties>'))
        z.writestr("word/document.xml", '<w:document xmlns:w="w"><w:body>%s</w:body></w:document>' %
                   "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % l for l in body.splitlines()))


def run(report_path: str) -> int:
    from . import analyzer, isolation, organizer, scanner
    from .capabilities import capabilities

    report = {"capabilities": capabilities(deep=True), "checks": {}, "records": [], "error": None}
    tmp = tempfile.mkdtemp(prefix="da_selftest_")
    ok = True
    try:
        src = os.path.join(tmp, "in")
        os.makedirs(src)
        _make_pdf(os.path.join(src, "paper.pdf"), EN_PAPER)
        _make_pdf(os.path.join(src, "scan_thesis.pdf"), ZH_THESIS, scanned=True)
        _make_docx(os.path.join(src, "report.docx"))
        with open(os.path.join(src, "song.mp3"), "wb") as fh:
            fh.write(b"ID3\x03\x00\x00\x00\x00\x00\x00")

        worker = isolation.IsolatedAnalyzer(analyzer.Options(ml_mode="always"))
        recs = []
        try:
            for r in scanner.scan(src):
                t0 = time.monotonic()
                r = worker.analyze(r)
                r.extra["seconds"] = "%.1f" % (time.monotonic() - t0)
                recs.append(r)
        finally:
            worker.close()
        by = {r.name: r for r in recs}
        report["records"] = [r.to_row() for r in recs]
        for r in recs:
            report["records"][recs.index(r)]["extra"] = dict(r.extra)

        paper, scan, docx = by["paper.pdf"], by["scan_thesis.pdf"], by["report.docx"]
        checks = {
            "paper_title": "Deep Learning Methods for Classifying Hearing Loss" in paper.title,
            "paper_authors": "John Smith" in paper.authors,
            "paper_type": paper.doc_type == "Journal Article",
            "paper_language": paper.language == "en",
            "paper_doi": paper.doi == "10.1016/j.heares.2019.107800",
            "scan_ocr": int(scan.extra.get("ocr_pages", "0")) >= 1,
            "scan_language": scan.language == "zh",
            "scan_title": "中文文本分类" in scan.title,
            "scan_type": scan.doc_type == "Dissertation-Thesis",
            "docx_title": docx.title == "Annual Water Quality Report 2022",
            "docx_type": docx.doc_type == "Report",
            "ml_model_used": any("ML model" in r.metadata_sources for r in recs),
        }
        out = os.path.join(tmp, "out")
        s = organizer.Settings(output_root=out, mode="copy", rename=True)
        organizer.plan(recs, s)
        log = organizer.execute(recs, s)
        checks["arranged"] = all(os.path.exists(r.destination) for r in recs)
        checks["undo"] = organizer.undo(log) == [] and not any(os.path.exists(r.destination) for r in recs)
        report["checks"] = checks
        ok = all(checks.values())
    except Exception:
        report["error"] = traceback.format_exc()
        ok = False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    report["passed"] = ok
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    return 0 if ok else 1
