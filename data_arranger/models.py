"""Data model shared by the scanner, extractors, organizer and GUI."""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


# Document types (the "doc type" classification).
JOURNAL = "Journal Article"
BOOK = "Book"
DISSERTATION = "Dissertation-Thesis"
CONFERENCE = "Conference Paper"
PATENT = "Patent"
STANDARD = "Standard"
REPORT = "Report"
NEWSPAPER = "Newspaper"
OTHER_DOC = "Unclassified"

DOC_TYPES = [JOURNAL, BOOK, DISSERTATION, CONFERENCE, PATENT, STANDARD,
             REPORT, NEWSPAPER, OTHER_DOC]

# Top-level categories.
CAT_DOCUMENT = "Documents"
CAT_IMAGE = "Images"
CAT_VIDEO = "Videos"
CAT_AUDIO = "Audio"
CAT_OTHER = "Other"

# Columns written to the catalog, in order.
CATALOG_FIELDS = [
    "status", "source", "destination", "category", "format", "size",
    "language", "language_name", "language_source",
    "doc_type", "doc_type_confidence", "doc_type_reasons",
    "title", "authors", "keywords", "abstract",
    "isbn", "issn", "doi", "publisher", "publish_date", "year",
    "volume", "issue", "pages", "journal", "conference", "institution",
    "degree", "patent_number", "standard_number",
    "duration", "width", "height", "artist", "album",
    "metadata_sources", "review", "notes",
]


@dataclass
class FileRecord:
    path: str                       # file, or folder for bundles
    rel_path: str                   # path relative to the scanned root
    ext: str = ""
    category: str = CAT_OTHER
    format: str = ""
    size: int = 0
    is_bundle: bool = False         # e.g. a folder of PDG page images
    companions: List[str] = field(default_factory=list)  # moved alongside

    language: str = ""              # ISO 639-1 code (zh, en, ...) or ""
    language_name: str = ""
    language_source: str = ""       # text / title / file tag / filename

    doc_type: str = ""
    doc_type_confidence: float = 0.0
    doc_type_reasons: List[str] = field(default_factory=list)

    title: str = ""
    authors: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    abstract: str = ""
    isbn: str = ""
    issn: str = ""
    doi: str = ""
    publisher: str = ""
    publish_date: str = ""
    volume: str = ""
    issue: str = ""
    pages: Optional[int] = None
    journal: str = ""
    conference: str = ""
    institution: str = ""
    degree: str = ""
    patent_number: str = ""
    standard_number: str = ""

    # Media properties
    duration: Optional[float] = None    # seconds
    width: Optional[int] = None
    height: Optional[int] = None
    artist: str = ""
    album: str = ""

    extra: Dict[str, str] = field(default_factory=dict)
    metadata_sources: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    review: List[str] = field(default_factory=list)   # reasons to double-check
    destination: str = ""
    status: str = "Pending"

    @property
    def name(self) -> str:
        import os
        return os.path.basename(self.path.rstrip("\\/"))

    @property
    def year(self) -> str:
        import re
        m = re.search(r"(1[5-9]\d\d|20\d\d)", self.publish_date or "")
        return m.group(1) if m else ""

    def set_if_empty(self, attr: str, value, source: str = "") -> bool:
        """Fill a field only if it has no value yet. Returns True if set."""
        if value in (None, "", [], 0):
            return False
        current = getattr(self, attr)
        if current not in (None, "", []):
            return False
        if isinstance(value, str):
            value = " ".join(value.split())
            if not value:
                return False
        setattr(self, attr, value)
        if source:
            self.extra["field_src_" + attr] = source
        if source and source not in self.metadata_sources:
            self.metadata_sources.append(source)
        return True

    def source_of(self, attr: str) -> str:
        return self.extra.get("field_src_" + attr, "")

    def to_row(self) -> Dict[str, str]:
        d = asdict(self)
        row = {}
        for key in CATALOG_FIELDS:
            if key == "source":
                val = self.path
            elif key == "year":
                val = self.year
            else:
                val = d.get(key, "")
            if isinstance(val, list):
                val = "; ".join(str(v) for v in val)
            elif val is None:
                val = ""
            elif isinstance(val, float):
                val = "%.2f" % val
            row[key] = str(val)
        return row
