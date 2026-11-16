"""Unit tests. Run with:  python -m unittest discover -s tests
They need only the standard library (optional dependencies are skipped)."""
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_arranger import analyzer, classify, identifiers as ids, organizer, scanner, textmeta  # noqa: E402
from data_arranger.extractors import cnki, office  # noqa: E402
from data_arranger.formats import identify  # noqa: E402
from data_arranger.language import detect_language  # noqa: E402
from data_arranger.models import (BOOK, DISSERTATION, JOURNAL, PATENT, STANDARD,  # noqa: E402
                                  FileRecord)


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arranger_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, data):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data.encode("utf-8") if isinstance(data, str) else data)
        return p


class IdentifierTests(unittest.TestCase):
    def test_isbn(self):
        self.assertEqual(ids.find_isbns("ISBN 978-7-111-54742-6 定价"), ["9787111547426"])
        self.assertEqual(ids.find_isbns("ISBN 0-306-40615-2"), ["9780306406157"])
        self.assertEqual(ids.find_isbns("ISBN 978-7-111-54742-7"), [])  # bad checksum

    def test_issn_doi(self):
        self.assertEqual(ids.find_issns("ISSN 0378-5955"), ["0378-5955"])
        self.assertEqual(ids.find_issns("ISSN 0378-5954"), [])
        self.assertEqual(ids.find_doi("https://doi.org/10.1016/j.heares.2019.107800."),
                         "10.1016/j.heares.2019.107800")

    def test_patent_standard(self):
        self.assertEqual(ids.find_patent_number("授权公告号 CN 109543210 B"), "CN 109543210 B")
        self.assertEqual(ids.find_standard_number("GB/T 7714-2015 信息与文献"), "GB/T 7714-2015")
        self.assertEqual(ids.find_standard_number("ISO 9001:2015 Quality"), "ISO 9001:2015")


class LanguageTests(unittest.TestCase):
    def test_scripts(self):
        self.assertEqual(detect_language("基于深度学习的图像识别方法研究")[0], "zh")
        self.assertEqual(detect_language("深層学習による画像認識の研究について")[0], "ja")
        self.assertEqual(detect_language("딥러닝 기반 이미지 인식 연구")[0], "ko")
        self.assertEqual(detect_language("Η γλώσσα είναι ελληνική")[0], "el")

    def test_latin(self):
        en = ("The results of this study show that the method is effective for the "
              "classification of images and that it can be used in practice by the community.")
        self.assertEqual(detect_language(en)[0], "en")
        de = ("Die Ergebnisse dieser Studie zeigen, dass die Methode für die Klassifikation "
              "von Bildern wirksam ist und dass sie in der Praxis von der Gemeinschaft genutzt wird.")
        self.assertEqual(detect_language(de)[0], "de")

    def test_short_latin_not_guessed(self):
        self.assertEqual(detect_language("IMG 2041")[0], "")

    def test_mixed_chinese_paper(self):
        text = "摘要：本文提出了一种新的方法。" * 5 + " Abstract: This paper proposes a new method. " * 3
        self.assertEqual(detect_language(text)[0], "zh")


class TextMetaTests(unittest.TestCase):
    PAPER = """Journal of Hearing Research, Vol. 12, No. 3, 2019
Deep Learning for Hearing Loss Classification
John Smith, Jane Doe and Wei Zhang
Department of Audiology, University of Somewhere
Abstract: We propose a convolutional network that classifies hearing loss from
audiograms with high accuracy. Results on three datasets are reported.
Keywords: deep learning; audiology; classification
1. Introduction
Hearing loss affects millions. ISSN 0378-5955 doi:10.1016/j.heares.2019.107800
"""

    def test_fill(self):
        rec = FileRecord(path="x.pdf", rel_path="x.pdf", ext="pdf", category="Documents")
        textmeta.fill_from_text(rec, self.PAPER)
        self.assertEqual(rec.title, "Deep Learning for Hearing Loss Classification")
        self.assertEqual(rec.authors, ["John Smith", "Jane Doe", "Wei Zhang"])
        self.assertEqual(rec.keywords, ["deep learning", "audiology", "classification"])
        self.assertTrue(rec.abstract.startswith("We propose a convolutional network"))
        self.assertEqual((rec.volume, rec.issue), ("12", "3"))
        self.assertEqual(rec.issn, "0378-5955")
        self.assertEqual(rec.doi, "10.1016/j.heares.2019.107800")
        self.assertTrue(rec.journal.startswith("Journal of Hearing Research"))

    def test_chinese_fields(self):
        text = ("计算机学报 2020年第3期 第43卷第3期\n基于深度学习的图像识别方法研究\n张三 李四 王五\n"
                "(清华大学 计算机系, 北京 100084)\n摘要：本文提出了一种基于卷积神经网络的图像识别方法，"
                "在多个数据集上取得了较好的效果，具有一定的应用价值和意义。\n关键词：深度学习；图像识别；卷积神经网络\n"
                "中图分类号：TP391")
        rec = FileRecord(path="x.pdf", rel_path="x.pdf", ext="pdf", category="Documents")
        textmeta.fill_from_text(rec, text)
        self.assertEqual(rec.keywords, ["深度学习", "图像识别", "卷积神经网络"])
        self.assertTrue(rec.abstract.startswith("本文提出了"))
        self.assertEqual((rec.volume, rec.issue), ("43", "3"))
        self.assertEqual(rec.title, "基于深度学习的图像识别方法研究")
        self.assertEqual(rec.authors, ["张三", "李四", "王五"])

    def test_filename(self):
        h = textmeta.parse_filename(r"C:\d\基于深度学习的图像识别方法研究_张三_李四.caj")
        self.assertEqual(h["title"], "基于深度学习的图像识别方法研究")
        self.assertEqual(h["authors"], ["张三", "李四"])
        h = textmeta.parse_filename("Donald Knuth - The Art of Computer Programming (1997).djvu")
        self.assertEqual(h["title"], "The Art of Computer Programming")
        self.assertEqual(h["year"], "1997")


class ClassifyTests(unittest.TestCase):
    def rec(self, **kw):
        r = FileRecord(path=kw.pop("path", "f.pdf"), rel_path="f.pdf", ext=kw.pop("ext", "pdf"),
                       category="Documents")
        for k, v in kw.items():
            setattr(r, k, v)
        return r

    def test_types(self):
        cases = [
            (self.rec(), TextMetaTests.PAPER, JOURNAL),
            (self.rec(pages=320), "图书在版编目(CIP)数据 ISBN 978-7-111-54742-6 第1版 第3次印刷 定价 59.00元", BOOK),
            (self.rec(pages=86), "硕士学位论文\n指导教师：王教授\n学科专业：计算机应用技术\n独创性声明", DISSERTATION),
            (self.rec(), "中华人民共和国国家标准 GB/T 7714-2015 ICS 01.140.20 2015-05-15发布 2015-12-01实施", STANDARD),
            (self.rec(), "(19)中华人民共和国国家知识产权局 (12)发明专利 (10)授权公告号 CN 109543210 B (21)申请号 201811234567.8", PATENT),
        ]
        for rec, text, expected in cases:
            textmeta.fill_from_text(rec, text)
            got, conf, why = classify.classify(rec, text)
            self.assertEqual(got, expected, "%s -> %s %s" % (text[:30], got, why))

    def test_extension_prior(self):
        rec = self.rec(ext="nh", path="论文_张三.nh")
        self.assertEqual(classify.classify(rec, "")[0], DISSERTATION)


class FormatTests(TempDirCase):
    def test_sniff_missing_extension(self):
        p = self.write("noext", b"%PDF-1.4\n...")
        self.assertEqual(identify(p)[:2], ("pdf", "Documents"))
        p = self.write("clip", b"RIFF\x00\x00\x00\x00AVI LIST")
        self.assertEqual(identify(p)[:2], ("avi", "Videos"))

    def test_misspelled_extensions(self):
        self.assertEqual(identify("a.mnpeg")[1], "Videos")
        self.assertEqual(identify("a.cag")[1], "Documents")


def make_docx(path, title, creator, body):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("docProps/core.xml", (
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>%s</dc:title><dc:creator>%s</dc:creator>'
            '<cp:keywords>alpha; beta</cp:keywords></cp:coreProperties>') % (title, creator))
        z.writestr("docProps/app.xml", '<Properties><Pages>7</Pages></Properties>')
        z.writestr("word/document.xml", (
            '<w:document xmlns:w="w"><w:body>%s</w:body></w:document>') %
            "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % line for line in body.splitlines()))


class ExtractorTests(TempDirCase):
    def test_docx(self):
        p = os.path.join(self.tmp, "report.docx")
        make_docx(p, "Annual Water Quality Report", "A. Smith; B. Jones",
                  "Research report\nThe annual report of the water quality in the region "
                  "is presented here for the year and the trends are discussed in detail.")
        rec = next(scanner.scan(self.tmp))
        analyzer.analyze(rec)
        self.assertEqual(rec.title, "Annual Water Quality Report")
        self.assertEqual(rec.authors, ["A. Smith", "B. Jones"])
        self.assertEqual(rec.keywords, ["alpha", "beta"])
        self.assertEqual(rec.pages, 7)
        self.assertEqual(rec.language, "en")

    def test_epub(self):
        p = os.path.join(self.tmp, "book.epub")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/c.opf"/></rootfiles></container>')
            z.writestr("OEBPS/c.opf", (
                '<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata>'
                '<dc:title>Les Misérables</dc:title><dc:creator>Victor Hugo</dc:creator>'
                '<dc:language>fr</dc:language><dc:publisher>Gallimard</dc:publisher>'
                '<dc:identifier>urn:isbn:9782070409228</dc:identifier></metadata>'
                '<manifest><item id="c1" href="c1.xhtml"/></manifest><spine><itemref idref="c1"/></spine></package>'))
            z.writestr("OEBPS/c1.xhtml", "<html><body><p>%s</p></body></html>" % (
                "Il était une fois un homme qui ne savait pas où aller dans la ville et qui marchait. " * 5))
        rec = next(scanner.scan(self.tmp))
        analyzer.analyze(rec)
        self.assertEqual((rec.title, rec.authors, rec.publisher, rec.isbn),
                         ("Les Misérables", ["Victor Hugo"], "Gallimard", "9782070409228"))
        self.assertEqual(rec.language, "fr")
        self.assertEqual(rec.doc_type, BOOK)

    def test_rtf(self):
        self.write("a.rtf", r"{\rtf1\ansi\ansicpg936{\fonttbl{\f0 SimSun;}}{\info{\title Test RTF}{\author Li Lei}}"
                            r"\f0 \u20013?\u25991? text\par second line}")
        rec = next(scanner.scan(self.tmp))
        text = office.extract_rtf(rec)
        self.assertEqual(rec.title, "Test RTF")
        self.assertIn("中文 text", text)

    def test_html_citation_meta(self):
        self.write("p.html", '<html lang="en"><head><meta charset="utf-8"><title>x</title>'
                             '<meta name="citation_title" content="A Study of Things">'
                             '<meta name="citation_author" content="Ann Lee">'
                             '<meta name="citation_author" content="Bo Chen">'
                             '<meta name="citation_journal_title" content="Nature">'
                             '<meta name="citation_doi" content="10.1038/abc123"></head><body>Hi</body></html>')
        os.makedirs(os.path.join(self.tmp, "p_files"))
        self.write("p_files/img.png", b"\x89PNG\r\n\x1a\n")
        recs = list(scanner.scan(self.tmp))
        self.assertEqual(len(recs), 1)           # the _files folder belongs to the page
        rec = recs[0]
        self.assertEqual(len(rec.companions), 1)
        analyzer.analyze(rec)
        self.assertEqual(rec.title, "A Study of Things")
        self.assertEqual(rec.authors, ["Ann Lee", "Bo Chen"])
        self.assertEqual(rec.doc_type, JOURNAL)
        self.assertEqual(rec.language, "en")

    def test_caj_toc(self):
        data = bytearray(b"CAJ FILE" + b"\x00" * 0x400)
        struct.pack_into("<i", data, 0x10, 12)
        struct.pack_into("<i", data, 0x110, 2)
        e1 = struct.pack("<256s24s12s12si", "城市交通拥堵治理策略研究".encode("gb18030"), b"1", b"", b"", 1)
        e2 = struct.pack("<256s24s12s12si", "1 引言".encode("gb18030"), b"2", b"", b"", 2)
        data[0x114:0x114 + 616] = e1 + e2
        self.write("城市交通拥堵治理_王强.caj", bytes(data))
        rec = next(scanner.scan(self.tmp))
        analyzer.analyze(rec)
        self.assertEqual(rec.pages, 12)
        self.assertEqual(rec.title, "城市交通拥堵治理策略研究")   # full TOC title beats truncated name
        self.assertEqual(rec.authors, ["王强"])
        self.assertEqual(rec.language, "zh")

    def test_kdh_decode_roundtrip(self):
        pdf = b"%PDF-1.4 test"
        key = (cnki.KDH_KEY * 10)[:len(pdf)]
        enc = b"KDH 1.00" + b"\x00" * (cnki.KDH_OFFSET - 8) + bytes(a ^ b for a, b in zip(pdf, key))
        self.assertEqual(cnki._kdh_decode(enc), pdf)

    def test_pdg_bundle(self):
        for i in range(1, 6):
            self.write("书/机器学习导论_12345678/%06d.pdg" % i, b"x")
        self.write("书/cover.jpg", b"\xff\xd8\xff")
        recs = list(scanner.scan(self.tmp))
        bundle = [r for r in recs if r.is_bundle][0]
        analyzer.analyze(bundle)
        self.assertEqual((bundle.title, bundle.pages, bundle.doc_type), ("机器学习导论", 5, BOOK))
        self.assertEqual(bundle.extra["ss_number"], "12345678")
        self.assertEqual(len(recs), 2)


class OrganizerTests(TempDirCase):
    def _setup(self):
        src = os.path.join(self.tmp, "in")
        os.makedirs(os.path.join(src, "a"))
        make_docx(os.path.join(src, "a", "report.docx"), "Annual Water Quality Report", "A. Smith",
                  "The annual report of the water quality in the region is presented here "
                  "for the year and the trends are discussed in detail by the authors.")
        self.write("in/b/song.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x00")
        self.write("in/b/report.docx", b"not really a docx")
        out = os.path.join(self.tmp, "out")
        recs = [analyzer.analyze(r) for r in scanner.scan(src, exclude=out)]
        return src, out, recs

    def test_sanitize(self):
        self.assertEqual(organizer.sanitize('a<b>:c?. '), "a_b__c_")
        self.assertEqual(organizer.sanitize("CON"), "_CON")

    def test_copy_then_undo(self):
        src, out, recs = self._setup()
        s = organizer.Settings(output_root=out, mode="copy", rename=True)
        organizer.plan(recs, s)
        dests = [r.destination for r in recs]
        self.assertEqual(len(set(dests)), len(dests))
        log = organizer.execute(recs, s)
        for r in recs:
            self.assertEqual(r.status, "Done", r.notes)
            self.assertTrue(os.path.exists(r.destination))
            self.assertTrue(os.path.exists(r.path))         # copy keeps originals
        named = [r for r in recs if r.title == "Annual Water Quality Report"][0]
        self.assertTrue(named.destination.endswith(
            os.path.join("Documents", "Report", "English", "A. Smith - Annual Water Quality Report.docx")),
            named.destination)
        with open(os.path.join(out, "catalog.csv"), encoding="utf-8-sig") as fh:
            self.assertEqual(len(fh.read().strip().splitlines()), 1 + len(recs))
        problems = organizer.undo(log)
        self.assertEqual(problems, [])
        self.assertFalse(any(os.path.exists(r.destination) for r in recs))

    def test_move_then_undo(self):
        src, out, recs = self._setup()
        originals = sorted(r.path for r in recs)
        s = organizer.Settings(output_root=out, mode="move")
        organizer.plan(recs, s)
        log = organizer.execute(recs, s)
        self.assertFalse(any(os.path.exists(p) for p in originals))
        self.assertFalse(os.path.exists(os.path.join(src, "a")))  # emptied folder removed
        with open(log, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["entries"]), len(recs))
        self.assertEqual(organizer.undo(log), [])
        self.assertTrue(all(os.path.exists(p) for p in originals))


if __name__ == "__main__":
    unittest.main()


class CajRebuildTests(unittest.TestCase):
    def test_rebuild_creates_page_tree_and_catalog(self):
        body = (b"CAJ junk header\x00\x01"
                b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>\nendobj\n"
                b"4 0 obj\n<< /Length 0 >>\nstream\n\nendstream\nendobj\n"
                b"5 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>\nendobj\n")
        out = cnki.rebuild_pdf(body)
        self.assertTrue(out.startswith(b"%PDF-1.4"))
        self.assertIn(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>", out)
        self.assertIn(b"/Type /Catalog /Pages 2 0 R", out)
        self.assertIn(b"trailer\n<< /Root 6 0 R", out)
        self.assertTrue(out.rstrip().endswith(b"%%EOF"))


class FusionTests(unittest.TestCase):
    TEXT = ("Journal of Hearing Research Vol. 12, No. 3 (2019)\n"
            "Deep Learning for Hearing Loss\nJohn Smith, Jane Doe\n"
            "Abstract We propose a network that classifies audiograms with high accuracy "
            "on three public datasets and several clinics.\nKeywords: audiology\n")

    def test_ml_values_must_be_in_text(self):
        from data_arranger import fusion
        rec = FileRecord(path="a.pdf", rel_path="a.pdf", ext="pdf", category="Documents")
        rec.set_if_empty("title", "a", "file name")
        fusion.merge(rec, {
            "doc_type": "Journal Article",
            "title": "Deep Learning for Hearing Loss",
            "authors": ["John Smith", "Invented Person"],
            "journal": "Journal of Hearing Research",
            "publisher": "Made Up Press",              # not in text -> rejected
            "isbn": "9780306406157",                   # valid checksum but not in text
            "publish_date": "2019", "volume": "12", "issue": "3",
            "abstract_start": "We propose a network that classifies audiograms",
            "keywords": ["audiology", "hallucinated"],
        }, self.TEXT)
        self.assertEqual(rec.title, "Deep Learning for Hearing Loss")
        self.assertEqual(rec.authors, [])          # only 1 of 2 verified (< 60%)
        self.assertEqual(rec.journal, "Journal of Hearing Research")
        self.assertEqual(rec.publisher, "")
        self.assertEqual(rec.isbn, "")
        self.assertEqual(rec.keywords, ["audiology"])
        self.assertTrue(rec.abstract.startswith("We propose a network"))
        self.assertEqual(rec.extra["type_from_llm"], "Journal Article")

    def test_ocr_noise_tolerated(self):
        from data_arranger import fusion
        t = fusion.norm("Deep Leaming for Hearinq Loss in Chi1dren")
        self.assertTrue(fusion.in_text("Deep Learning for Hearing Loss in Children", t))


class IsolationTests(TempDirCase):
    def test_worker_process_roundtrip_and_fallback(self):
        from data_arranger import analyzer, isolation
        make_docx(os.path.join(self.tmp, "r.docx"), "Annual Water Quality Report", "A. Smith",
                  "The annual report of the water quality in the region is presented here "
                  "for the year and the trends are discussed in detail by the authors.")
        rec = next(scanner.scan(self.tmp))
        w = isolation.IsolatedAnalyzer(analyzer.Options(ml_mode="off"), timeout=60)
        try:
            out = w.analyze(rec)
        finally:
            w.close()
        self.assertEqual(out.title, "Annual Water Quality Report")
        self.assertEqual(out.language, "en")

        rec2 = FileRecord(path=os.path.join(self.tmp, "论文_张三.caj"), rel_path="论文_张三.caj",
                          ext="caj", category="Documents")
        isolation.basic_analysis(rec2, "simulated crash")
        self.assertEqual(rec2.status, "Needs review")
        self.assertIn("content could not be read", rec2.review)
