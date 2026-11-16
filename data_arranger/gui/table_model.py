from typing import List

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt5.QtGui import QBrush, QColor

from ..models import FileRecord

COLUMNS = [
    ("Status", lambda r: r.status),
    ("File", lambda r: r.name + ("  [folder]" if r.is_bundle else "")),
    ("Category", lambda r: r.category),
    ("Format", lambda r: r.format),
    ("Language", lambda r: r.language_name),
    ("Doc type", lambda r: r.doc_type),
    ("Title", lambda r: r.title),
    ("Authors", lambda r: "; ".join(r.authors)),
    ("Year", lambda r: r.year),
    ("Journal / Publisher", lambda r: r.journal or r.conference or r.publisher),
    ("Pages", lambda r: "" if r.pages is None else str(r.pages)),
    ("Size", lambda r: human_size(r.size)),
    ("Destination", lambda r: r.destination),
]
COL_STATUS, COL_SIZE = 0, 11

_STATUS_COLORS = {"Done": "#2e7d32", "Error": "#c62828", "Skipped": "#9e9e9e",
                  "Needs review": "#e65100"}


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return str(n)


class RecordModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.records: List[FileRecord] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section][0]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        rec = self.records[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            return COLUMNS[col][1](rec)
        if role == Qt.ToolTipRole:
            if col == COL_STATUS and (rec.notes or rec.review):
                parts = []
                if rec.review:
                    parts.append("Please check: " + ", ".join(rec.review))
                return "\n".join(parts + rec.notes)
            if col == 5 and rec.doc_type_reasons:
                return "Confidence %.0f%%\n%s" % (rec.doc_type_confidence * 100,
                                                  "\n".join(rec.doc_type_reasons))
            if col == 4 and rec.language_source:
                return "Detected from " + rec.language_source
            return rec.path if col == 1 else COLUMNS[col][1](rec)
        if role == Qt.ForegroundRole and col == COL_STATUS:
            c = _STATUS_COLORS.get(rec.status)
            return QBrush(QColor(c)) if c else None
        if role == Qt.UserRole:          # sort key
            if col == COL_SIZE:
                return rec.size
            if col == 10:
                return rec.pages or 0
            return (COLUMNS[col][1](rec) or "").lower()
        return None

    def add_records(self, recs: List[FileRecord]):
        if not recs:
            return
        start = len(self.records)
        self.beginInsertRows(QModelIndex(), start, start + len(recs) - 1)
        self.records.extend(recs)
        self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self.records = []
        self.endResetModel()

    def refresh(self):
        if self.records:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.records) - 1, len(COLUMNS) - 1))

    def refresh_record(self, rec: FileRecord):
        try:
            row = self.records.index(rec)
        except ValueError:
            return
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))


class FilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.category = ""
        self.doc_type = ""
        self.language = ""
        self.review_only = False
        self.setSortRole(Qt.UserRole)

    def set_filters(self, text=None, category=None, doc_type=None, language=None, review_only=None):
        if review_only is not None:
            self.review_only = review_only
        if text is not None:
            self.text = text.lower()
        if category is not None:
            self.category = category
        if doc_type is not None:
            self.doc_type = doc_type
        if language is not None:
            self.language = language
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        rec = self.sourceModel().records[row]
        if self.review_only and not rec.review:
            return False
        if self.category and rec.category != self.category:
            return False
        if self.doc_type and rec.doc_type != self.doc_type:
            return False
        if self.language and rec.language_name != self.language:
            return False
        if self.text:
            hay = " ".join([rec.path, rec.title, " ".join(rec.authors), rec.journal,
                            rec.publisher, " ".join(rec.keywords), rec.doi, rec.isbn]).lower()
            return self.text in hay
        return True
