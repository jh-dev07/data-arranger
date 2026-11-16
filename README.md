# Data Arranger

A Windows desktop app (Python 3.8 + PyQt5) that sorts a folder full of documents, images, videos and audio into an organised folder tree. It detects each file's **language**. For documents it also extracts **bibliographic metadata** and classifies the **document type**: journal article, book, dissertation/thesis, conference paper, patent, standard, report or newspaper.

## How to use

1. **Source folder**: choose or drag in the folder that holds the files and sub-folders.
2. **Output folder**: where the arranged files go. The default is `<source>_arranged` next to the source.
3. **Scan & analyze**: every file is read. The table shows the category, format, language, doc type, title, authors, year, journal or publisher, page count and the planned destination. Hover a cell to see why a value was chosen. Select a row to see every field in the *Details* tab, where you can correct the doc type, language, title and other values before arranging.
4. **Arrange files**: files are **copied** (the default) or **moved**.
5. **Undo last arrangement**: puts moved files back, or deletes the copies. Every run writes a log to `<output>/_arranger_logs/`.

Options:
* **Rename files from metadata**: for example `2019 - Smith et al. - Deep learning for X.pdf`.
* **Online lookup by DOI/ISBN**: optional and off by default. It looks up the DOI in Crossref and the ISBN in Open Library. Only the identifier is sent.
* **Folder layout**: the templates are editable. The defaults are:

```
Documents/{doctype}/{language}/…      e.g. Documents/Journal Article/Chinese/
Images|Videos|Audio/{language}/…      e.g. Audio/Russian/
Other/{ext}/…
```

The available fields are `{category} {doctype} {language} {lang} {format} {ext} {year} {title} {authors} {journal} {publisher} {source_folder}`.

`<output>/catalog.csv` holds every arranged file with its metadata. It is UTF-8 with a BOM, so Excel shows Chinese and other non-Latin text correctly. *Export catalog…* saves the current table as CSV or JSON at any time.

## Formats

| Category | Extensions | What is read |
|---|---|---|
| PDF | pdf | Info dictionary, XMP (DOI, journal, volume, issue, ISSN), text of the first 4 pages and the last page, and the title from the largest font on page 1 |
| Word / PowerPoint / Excel | doc docx wps ppt pptx pps xls xlsx rtf odt odp | Document properties (title, author, keywords, pages) and body text. Legacy .doc text is read through the Word piece table. |
| CNKI | caj kdh nh teb caa (and `.cag`) | CAJ files that are really PDFs are read fully. KDH is decoded to PDF in memory. CAJ/HN: page count, table of contents (usually including the title), and the CNKI file name `Title_Author.caj` |
| Chaoxing | pdg | A folder of .pdg pages is treated as **one book** and moved as a whole. The title and SS number come from the folder name. |
| CHM | chm | Title and Windows language ID from the CHM header |
| DjVu | djvu djv | Page count and uncompressed text and annotations. With [DjVuLibre](https://djvu.sourceforge.net/) installed, full metadata and the text layer are read as well. |
| E-books | epub mobi azw3 fb2 | Title, author, publisher, date, ISBN, language |
| Web / XML / text | html htm mht xml txt md tex | `citation_*`, Dublin Core and `<html lang>` meta tags. A saved page's `name_files` folder moves with it. XML: JATS, TEI and Dublin Core. |
| Images | jpg jpeg png bmp gif tif webp heic svg … | Dimensions and EXIF (date taken, camera, title, keywords) |
| Video | mp4 avi mpg mpeg (also `.mnpeg`) mkv mov wmv flv webm 3gp rm rmvb … | Duration, resolution, title, track language |
| Audio | wav wma mp3 ogg flac m4a aac ape amr … | Duration, bitrate, title, artist, album, ID3 language |

Files with other extensions go under `Other/`. A file with no extension or the wrong one is recognised by its content where possible.

### How the language is determined
The app checks these sources in order: the document text, the language tag in the file (Office, EPUB, CHM or ID3), the title and keywords, and finally the file name. Chinese, Japanese, Korean, Russian, Greek, Arabic and other non-Latin scripts are recognised from just a few characters. Text in Latin script is identified with `langdetect`. A short Latin file name such as `IMG_2041` is marked *Unknown* rather than guessed.

### How the document type is chosen
The type is decided by weighted rules, and the reasons are shown in the UI:
* ISBN: book
* ISSN, CN serial number, or volume and issue: journal article
* Patent and standard numbers
* Wording on the first pages, such as `学位论文`, `指导教师`, `submitted in partial fulfillment`, `Proceedings of`, `中华人民共和国国家标准` or `发明专利`
* Format: `.nh` is a CNKI thesis and `.pdg` is a book
* Page count
* A Crossref record type, if the online lookup is enabled

## Limitations
* **OCR is not performed.** Scanned PDFs, CNKI HN/CAJ page images and PDG pages have no text layer. For these files the metadata comes from the header, table of contents, folder name or file name. The *Notes* field says when this happened.
* Encrypted CAJ text streams and CHM page text (LZX-compressed) are not decoded.
* Heuristic metadata is right for most papers, theses and books with a normal layout, but it will sometimes be wrong. Review the table before arranging, and use the online lookup when files have DOIs or ISBNs.

## Running and building (Windows)

You need Python **3.8** installed, with the `py` launcher.

* Run from source: double-click `run_windows.bat`. The first run creates `.venv` and installs `requirements.txt`.
* Build a stand-alone app: double-click `build_windows.bat`. The result is `dist\DataArranger\DataArranger.exe`, and the whole `dist\DataArranger` folder can be copied to another PC.

Manual equivalent:

```bat
py -3.8 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run.py
```

## Tests

```
python -m unittest discover -s tests
```

## Project layout

```
data_arranger/
  formats.py        extension and magic-byte identification
  language.py       script analysis + langdetect + LCID/tag mapping
  identifiers.py    ISBN / ISSN / DOI / CN / patent / standard numbers (checksummed)
  textmeta.py       abstract, keywords, volume/issue, journal, title/author heuristics
  classify.py       document-type scoring
  enrich.py         optional Crossref / Open Library lookup
  analyzer.py       per-file pipeline
  scanner.py        folder walk, PDG book bundles, HTML companion folders
  organizer.py      destination templates, copy/move, catalog, undo
  extractors/       pdf, office (OOXML/OLE/RTF/EPUB/MOBI/FB2), cnki, chm, djvu, markup, media
  gui/              PyQt5 main window, table model, dialogs, worker threads
```
