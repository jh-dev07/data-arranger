# Data Arranger

A Windows desktop app (Python 3.8 + PyQt5) that sorts a folder of documents, images, videos and audio into an organised folder tree.

For **documents**, it does three things, fully offline:
- detects the **language**
- extracts **bibliographic metadata**: title, authors, keywords, abstract, ISBN/ISSN/DOI, publisher, date, volume, issue, pages, journal, conference, institution and degree
- classifies the **document type**: journal article, book, dissertation/thesis, conference paper, patent, standard, report, newspaper, or unclassified

Images, videos and audio are arranged by category and format.

## Download

Get the latest build from the repository's **Releases → Latest Windows build** (`DataArranger-windows.zip`, about 1.5 GB). Unzip it and run `DataArranger\DataArranger.exe`, keeping the whole folder together because the `models` and `tools` folders are needed. No installation or internet connection is required.

Requirements: 64-bit Windows 10/11, a CPU with AVX2 (most CPUs made since 2013), and 4 GB of RAM (8 GB recommended).

## How to use

1. **Source folder**: choose or drag in the folder holding the files and sub-folders.
2. **Output folder**: where the arranged files go. The default is `<source>_arranged`.
3. **Scan & analyze**: every file is read. The table shows the category, format, language, doc type, title, authors, year, journal or publisher, page count and planned destination.
   - Orange **Needs review** rows are documents the app is unsure about. Hover over the status to see why, or tick *Needs review only* to list just those rows.
   - Select a row to see every field and correct it before arranging.
4. **Arrange files**: files are **copied** (the default) or **moved**.
5. **Undo last arrangement**: puts moved files back, or deletes the copies. Every run writes a log to `<output>/_arranger_logs/`.

`<output>/catalog.csv` lists every arranged file with all of its metadata. It's UTF-8 encoded and opens correctly in Excel.

Default folder layout (editable under *Folder layout…*):

```
Documents/{doctype}/{language}/       e.g. Documents/Dissertation-Thesis/Chinese/
Images|Videos|Audio/{format}/         e.g. Videos/MP4/
Other/{ext}/
```

### ML model setting
- **Always** (default, most accurate): the local model reads every document. This takes about 10–40 seconds per document, depending on the CPU.
- **Only when rules are unsure**: faster. The model runs only when the title, authors or doc type couldn't be determined reliably by rules.
- **Off**: rules and OCR only, which is the fastest.

The status bar shows which engines are available: OCR, ML model, language model, PDF, CHM and DjVu.

## How it works

| Step | Engine |
|---|---|
| Text from PDFs | PyMuPDF: text layer, font sizes for the title, info dictionary and XMP |
| Image-only pages | **OCR** with RapidOCR (PaddleOCR PP-OCRv4 models on ONNX Runtime). Reads Chinese and English, including mixed pages. Used for scanned and image-only PDFs, KDH, rebuilt CAJ, image pages in HN files, PDG pages and DjVu pages. |
| Metadata and doc type | **Local ML model** Qwen2.5-1.5B-Instruct (GGUF Q4_K_M) running on llama.cpp. Its output is forced into valid JSON. |
| Hallucination guard | Every value from the model must appear in the document text (allowing for OCR errors). Identifiers must also pass their ISBN or ISSN checksum. |
| Rules | Identifier detection (checksummed ISBN, ISSN, DOI, CN serial, patent and standard numbers), abstract and keyword sections, volume and issue, CIP blocks, thesis cover pages, standard and patent headers |
| Doc type | A weighted vote of identifiers, rules, file format and the ML model. The reasons and a confidence score are shown in the app. |
| Language (documents) | fastText `lid.176` (176 languages), plus script analysis for CJK and other scripts |
| Robustness | Each file is analysed in a separate worker process with a time limit. If a damaged file hangs or crashes a library, that file falls back to name-based analysis and the scan continues. If an engine keeps crashing on a PC, it is switched off for the rest of the run. |

### Formats

| Formats | What is read |
|---|---|
| PDF | Text, OCR, embedded metadata, XMP |
| DOC DOCX WPS RTF ODT, PPT PPTX ODP, XLS XLSX | Document properties and body text |
| CAJ, KDH, NH/HN, TEB, CAA (and `.cag`) | Real PDFs are read fully. KDH is decoded. CAJ page objects are rebuilt into a PDF. HN: table of contents, page count and OCR of JPEG pages. CNKI file names `Title_Author.caj` are also used. |
| PDG (Chaoxing) | A folder of pages is treated as one book. The copyright page (CIP, ISBN, publisher), title page and cover are OCR'd. |
| CHM | Title and language from the header; page text via the bundled 7-Zip |
| DjVu | Metadata and text layer via the bundled DjVuLibre; OCR when there's no text layer |
| EPUB, MOBI/AZW3, FB2 | Embedded book metadata and text |
| HTML/MHT, XML, TXT/MD/TeX | Meta tags (`citation_*`, Dublin Core), JATS/TEI XML, plain text |
| Images, video, audio | Arranged by category and format; EXIF and tag data are kept in the catalog |

### Known limits
- HN pages compressed with CNKI's JBIG variant and **encrypted** PDG pages can't be decoded without the vendor software. These files use their table of contents, folder name and file name, and are flagged for review.
- OCR covers Chinese and English. Scanned pages in other scripts (Japanese kana, Korean, Cyrillic, Arabic…) are read less reliably. Digital (non-scanned) documents in any language are fine.
- No automatic extraction is right 100% of the time. That's why uncertain results are flagged for review and can be edited before arranging.

## Building

The GitHub Actions workflow (`.github/workflows/build-windows.yml`) builds everything on Windows. It runs the unit tests and the full OCR and ML test, builds the `.exe`, runs `DataArranger.exe --selftest` on the built app, and publishes the zip as a release.

To build locally, run `build_windows.bat`. You need Python 3.8, Visual Studio Build Tools (C++), CMake and 7-Zip.

To run from source:

```bat
py -3.8 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python download_models.py
.venv\Scripts\python run.py
```

## Tests

```
python -m unittest discover -s tests              # fast tests
set DATA_ARRANGER_FULL=1 && python -m unittest tests.test_full   # real OCR + ML
DataArranger.exe --selftest result.json           # check an installed build
```

## Licenses of bundled components
- Qwen2.5 model: Apache-2.0
- fastText lid.176: CC BY-SA 3.0
- RapidOCR and PP-OCR models: Apache-2.0
- llama.cpp: MIT
- PyMuPDF: AGPL-3.0
- 7-Zip: LGPL
- DjVuLibre: GPL-2.0
- PyQt5: GPL-3.0

PyMuPDF, DjVuLibre and PyQt5 are copyleft licenses. Distributing this app to others requires complying with them (for example, by publishing its source) or buying commercial licenses.
