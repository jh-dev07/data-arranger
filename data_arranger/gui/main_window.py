import os
import subprocess
import sys
from collections import Counter

from PyQt5.QtCore import QSettings, Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QAbstractItemView, QAction, QButtonGroup, QCheckBox,
                             QComboBox, QFileDialog, QFormLayout, QGroupBox,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                             QPushButton, QRadioButton, QScrollArea, QSplitter,
                             QTableView, QTabWidget, QVBoxLayout, QWidget)

from .. import __version__, organizer
from ..language import LANGUAGE_NAMES
from ..models import (CAT_AUDIO, CAT_DOCUMENT, CAT_IMAGE, CAT_OTHER, CAT_VIDEO,
                      DOC_TYPES, FileRecord)
from .dialogs import FormatsDialog, LayoutDialog
from .table_model import FilterProxy, RecordModel
from .workers import ArrangeWorker, ScanWorker, UndoWorker

APP = "Data Arranger"


class DetailPanel(QWidget):
    """Shows all metadata of the selected file and lets the user correct it."""

    EDIT_FIELDS = [
        ("title", "Title"), ("authors", "Authors (; separated)"),
        ("publish_date", "Publish date"), ("journal", "Journal"),
        ("conference", "Conference"), ("publisher", "Publisher"),
        ("volume", "Volume"), ("issue", "Issue"), ("isbn", "ISBN"), ("issn", "ISSN"),
        ("doi", "DOI"), ("institution", "Institution"), ("degree", "Degree"),
        ("patent_number", "Patent no."), ("standard_number", "Standard no."),
        ("keywords", "Keywords (; separated)"),
    ]

    def __init__(self, on_save, parent=None):
        super().__init__(parent)
        self.on_save = on_save
        self.rec = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        form = QFormLayout(inner)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.info = QLabel("Select a file to see its details.")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow(self.info)

        self.doc_type = QComboBox()
        self.doc_type.addItems([""] + DOC_TYPES)
        form.addRow("Doc type", self.doc_type)
        self.language = QComboBox()
        self.language.setEditable(True)
        self.language.addItems(["Unknown"] + sorted(set(LANGUAGE_NAMES.values())))
        form.addRow("Language", self.language)

        self.edits = {}
        for attr, label in self.EDIT_FIELDS:
            w = QLineEdit()
            self.edits[attr] = w
            form.addRow(label, w)
        self.abstract = QPlainTextEdit()
        self.abstract.setFixedHeight(110)
        form.addRow("Abstract", self.abstract)

        self.readonly = QLabel()
        self.readonly.setWordWrap(True)
        self.readonly.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow(self.readonly)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        row = QHBoxLayout()
        self.save_btn = QPushButton("Save changes")
        self.save_btn.clicked.connect(self._save)
        open_btn = QPushButton("Open file")
        open_btn.clicked.connect(lambda: self.rec and open_path(self.rec.path))
        folder_btn = QPushButton("Show in folder")
        folder_btn.clicked.connect(lambda: self.rec and reveal(self.rec.path))
        for b in (self.save_btn, open_btn, folder_btn):
            row.addWidget(b)
        outer.addLayout(row)
        self.setEnabled(False)

    def show_record(self, rec):
        self.rec = rec
        self.setEnabled(rec is not None)
        if rec is None:
            self.info.setText("Select a file to see its details.")
            return
        self.info.setText("<b>%s</b><br>%s · %s · %s" % (
            esc(rec.name), esc(rec.category), esc(rec.format),
            ("%d pages" % rec.pages) if rec.pages else ""))
        self.doc_type.setCurrentText(rec.doc_type)
        self.doc_type.setEnabled(rec.category == CAT_DOCUMENT)
        self.language.setCurrentText(rec.language_name or "Unknown")
        for attr, w in self.edits.items():
            v = getattr(rec, attr)
            w.setText("; ".join(v) if isinstance(v, list) else (v or ""))
            w.setCursorPosition(0)
        self.abstract.setPlainText(rec.abstract)
        lines = [
            ("Source", rec.path),
            ("Destination", rec.destination),
            ("Language detected from", rec.language_source or "-"),
            ("Doc type confidence", "%.0f%%" % (rec.doc_type_confidence * 100) if rec.doc_type else "-"),
            ("Doc type evidence", "; ".join(rec.doc_type_reasons) or "-"),
            ("Metadata sources", ", ".join(rec.metadata_sources) or "-"),
        ]
        if rec.duration:
            m, s = divmod(int(rec.duration), 60)
            lines.append(("Duration", "%d:%02d" % (m, s)))
        if rec.width:
            lines.append(("Dimensions", "%d × %d" % (rec.width, rec.height or 0)))
        if rec.artist or rec.album:
            lines.append(("Artist / album", " / ".join(x for x in (rec.artist, rec.album) if x)))
        for k, v in sorted(rec.extra.items()):
            if k != "type_from_lookup" and v:
                lines.append((k.replace("_", " ").capitalize(), str(v)[:300]))
        if rec.notes:
            lines.append(("Notes", " | ".join(rec.notes)))
        self.readonly.setText("<br>".join("<b>%s:</b> %s" % (esc(k), esc(v)) for k, v in lines))

    def _save(self):
        rec = self.rec
        if rec is None:
            return
        for attr, w in self.edits.items():
            text = w.text().strip()
            if isinstance(getattr(rec, attr), list):
                setattr(rec, attr, [x.strip() for x in text.split(";") if x.strip()])
            else:
                setattr(rec, attr, text)
        rec.abstract = self.abstract.toPlainText().strip()
        if rec.category == CAT_DOCUMENT:
            new_type = self.doc_type.currentText()
            if new_type != rec.doc_type:
                rec.doc_type = new_type
                rec.doc_type_confidence = 1.0
                rec.doc_type_reasons = ["set by user"]
        lang_name = self.language.currentText().strip()
        if lang_name != rec.language_name:
            code = next((c for c, n in LANGUAGE_NAMES.items() if n == lang_name), "")
            rec.language, rec.language_name, rec.language_source = code, lang_name or "Unknown", "set by user"
        if "edited by user" not in rec.metadata_sources:
            rec.metadata_sources.append("edited by user")
        self.on_save(rec)


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def open_path(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def reveal(path):
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_path(os.path.dirname(path.rstrip("\\/")) or path)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("%s %s" % (APP, __version__))
        self.resize(1400, 860)
        self.setAcceptDrops(True)
        self.qs = QSettings("DataArranger", "DataArranger")
        self.settings = organizer.Settings()
        self._load_settings()
        self.worker = None

        self.model = RecordModel(self)
        self.proxy = FilterProxy(self)
        self.proxy.setSourceModel(self.model)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.addWidget(self._build_folder_box())
        root.addLayout(self._build_action_row())
        root.addLayout(self._build_filter_row())

        split = QSplitter(Qt.Horizontal)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.verticalHeader().hide()
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        for col, width in enumerate([70, 240, 85, 110, 80, 130, 280, 160, 50, 180, 50, 75, 380]):
            self.table.setColumnWidth(col, width)
        self.table.selectionModel().currentRowChanged.connect(self._on_row)
        self.table.doubleClicked.connect(self._on_double_click)
        split.addWidget(self.table)

        tabs = QTabWidget()
        self.detail = DetailPanel(self._on_record_edited)
        tabs.addTab(self.detail, "Details")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        tabs.addTab(self.log, "Log")
        self.tabs = tabs
        split.addWidget(tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([1000, 400])
        root.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.summary = QLabel("")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(360)
        self.progress.setTextVisible(True)
        self.status = QLabel("Choose a folder to begin.")
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(self.status)
        bottom.addWidget(self.progress)
        root.addLayout(bottom)

        self._build_menu()
        self._update_buttons()

    # ----- layout ----------------------------------------------------------------
    def _build_folder_box(self):
        box = QGroupBox("Folders")
        form = QFormLayout(box)
        self.src_edit = QLineEdit(self.qs.value("source", ""))
        self.src_edit.setPlaceholderText("Folder containing the files and sub-folders to arrange")
        b1 = QPushButton("Browse…")
        b1.clicked.connect(self._browse_src)
        r1 = QHBoxLayout()
        r1.addWidget(self.src_edit)
        r1.addWidget(b1)
        form.addRow("Source folder:", r1)

        self.out_edit = QLineEdit(self.qs.value("output", ""))
        self.out_edit.setPlaceholderText("Where arranged files go (default: <source>_arranged next to the source)")
        b2 = QPushButton("Browse…")
        b2.clicked.connect(self._browse_out)
        r2 = QHBoxLayout()
        r2.addWidget(self.out_edit)
        r2.addWidget(b2)
        form.addRow("Output folder:", r2)

        opts = QHBoxLayout()
        self.copy_rb = QRadioButton("Copy (keep originals)")
        self.move_rb = QRadioButton("Move")
        grp = QButtonGroup(self)
        grp.addButton(self.copy_rb)
        grp.addButton(self.move_rb)
        (self.move_rb if self.settings.mode == "move" else self.copy_rb).setChecked(True)
        self.copy_rb.toggled.connect(self._options_changed)
        self.rename_cb = QCheckBox("Rename files from metadata")
        self.rename_cb.setChecked(self.settings.rename)
        self.rename_cb.setToolTip("New name follows the template in Folder layout…, e.g. "
                                  "\"2019 - Smith et al. - Deep learning.pdf\"")
        self.rename_cb.toggled.connect(self._options_changed)
        self.online_cb = QCheckBox("Online lookup by DOI/ISBN")
        self.online_cb.setChecked(self.qs.value("online", False, type=bool))
        self.online_cb.setToolTip("Queries Crossref (DOI) and Open Library (ISBN) for accurate metadata.\n"
                                  "Only the identifier is sent. Applies to the next scan.")
        layout_btn = QPushButton("Folder layout…")
        layout_btn.clicked.connect(self._edit_layout)
        for w in (self.copy_rb, self.move_rb, self.rename_cb, self.online_cb):
            opts.addWidget(w)
        opts.addStretch(1)
        opts.addWidget(layout_btn)
        form.addRow("Options:", opts)
        self.out_edit.editingFinished.connect(self._options_changed)
        return box

    def _build_action_row(self):
        row = QHBoxLayout()
        self.scan_btn = QPushButton("1. Scan && analyze")
        self.scan_btn.clicked.connect(self.start_scan)
        self.arrange_btn = QPushButton("2. Arrange files")
        self.arrange_btn.clicked.connect(self.start_arrange)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_worker)
        self.undo_btn = QPushButton("Undo last arrangement")
        self.undo_btn.clicked.connect(self.start_undo)
        self.export_btn = QPushButton("Export catalog…")
        self.export_btn.clicked.connect(self.export_catalog)
        for b in (self.scan_btn, self.arrange_btn, self.stop_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.export_btn)
        row.addWidget(self.undo_btn)
        return row

    def _build_filter_row(self):
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by file name, title, author, journal, keyword, DOI, ISBN…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda t: self.proxy.set_filters(text=t))
        self.cat_combo = QComboBox()
        self.cat_combo.addItems(["All categories", CAT_DOCUMENT, CAT_IMAGE, CAT_VIDEO, CAT_AUDIO, CAT_OTHER])
        self.cat_combo.currentIndexChanged.connect(
            lambda i: self.proxy.set_filters(category="" if i == 0 else self.cat_combo.currentText()))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["All doc types"] + DOC_TYPES)
        self.type_combo.currentIndexChanged.connect(
            lambda i: self.proxy.set_filters(doc_type="" if i == 0 else self.type_combo.currentText()))
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("All languages")
        self.lang_combo.currentIndexChanged.connect(
            lambda i: self.proxy.set_filters(language="" if i <= 0 else self.lang_combo.currentText()))
        row.addWidget(self.search, 1)
        row.addWidget(self.cat_combo)
        row.addWidget(self.type_combo)
        row.addWidget(self.lang_combo)
        return row

    def _build_menu(self):
        m = self.menuBar().addMenu("&File")
        a = QAction("Choose source folder…", self)
        a.triggered.connect(self._browse_src)
        m.addAction(a)
        a = QAction("Export catalog…", self)
        a.triggered.connect(self.export_catalog)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Exit", self)
        a.triggered.connect(self.close)
        m.addAction(a)
        m = self.menuBar().addMenu("&Settings")
        a = QAction("Folder layout and naming…", self)
        a.triggered.connect(self._edit_layout)
        m.addAction(a)
        m = self.menuBar().addMenu("&Help")
        a = QAction("Supported formats", self)
        a.triggered.connect(lambda: FormatsDialog(self).exec_())
        m.addAction(a)
        a = QAction("About", self)
        a.triggered.connect(self._about)
        m.addAction(a)

    # ----- settings ------------------------------------------------------------------
    def _load_settings(self):
        s = self.settings
        s.mode = self.qs.value("mode", s.mode)
        s.rename = self.qs.value("rename", s.rename, type=bool)
        s.doc_template = self.qs.value("doc_template", s.doc_template)
        s.media_template = self.qs.value("media_template", s.media_template)
        s.other_template = self.qs.value("other_template", s.other_template)
        s.rename_template = self.qs.value("rename_template", s.rename_template)
        s.remove_empty_dirs = self.qs.value("remove_empty_dirs", s.remove_empty_dirs, type=bool)

    def _save_settings(self):
        s = self.settings
        for k in ("mode", "rename", "doc_template", "media_template", "other_template",
                  "rename_template", "remove_empty_dirs"):
            self.qs.setValue(k, getattr(s, k))
        self.qs.setValue("source", self.src_edit.text())
        self.qs.setValue("output", self.out_edit.text())
        self.qs.setValue("online", self.online_cb.isChecked())

    def _output_root(self):
        out = self.out_edit.text().strip()
        if out:
            return os.path.abspath(out)
        src = self.src_edit.text().strip().rstrip("\\/")
        return os.path.abspath(src + "_arranged") if src else ""

    def _options_changed(self, *_):
        self.settings.mode = "move" if self.move_rb.isChecked() else "copy"
        self.settings.rename = self.rename_cb.isChecked()
        self._replan()

    def _replan(self):
        self.settings.output_root = self._output_root()
        if self.model.records and self.settings.output_root:
            pending = [r for r in self.model.records if r.status != "Done"]
            organizer.plan(pending, self.settings)
            self.model.refresh()
            if self.detail.rec:
                self.detail.show_record(self.detail.rec)

    def _edit_layout(self):
        dlg = LayoutDialog(self.settings, self)
        if dlg.exec_():
            dlg.apply_to(self.settings)
            self._save_settings()
            self._replan()

    def _browse_src(self):
        d = QFileDialog.getExistingDirectory(self, "Choose source folder", self.src_edit.text())
        if d:
            self.src_edit.setText(os.path.normpath(d))
            self._replan()

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "Choose output folder",
                                             self.out_edit.text() or self.src_edit.text())
        if d:
            self.out_edit.setText(os.path.normpath(d))
            self._replan()

    # ----- drag & drop -------------------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if os.path.isdir(p):
                self.src_edit.setText(os.path.normpath(p))
                self._replan()
                break

    # ----- actions ------------------------------------------------------------------
    def _busy(self):
        return self.worker is not None and self.worker.isRunning()

    def _update_buttons(self):
        busy = self._busy()
        has = bool(self.model.records)
        self.scan_btn.setEnabled(not busy)
        self.arrange_btn.setEnabled(not busy and has)
        self.export_btn.setEnabled(not busy and has)
        self.undo_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy and isinstance(self.worker, (ScanWorker, ArrangeWorker)))

    def logmsg(self, text):
        self.log.appendPlainText(text)

    def start_scan(self):
        src = self.src_edit.text().strip()
        if not src or not os.path.isdir(src):
            QMessageBox.warning(self, APP, "Please choose an existing source folder.")
            return
        out = self._output_root()
        if organizer.same_location(src, out):
            QMessageBox.warning(self, APP, "The output folder must be different from the source folder.")
            return
        self._save_settings()
        self.settings.output_root = out
        self.model.clear()
        self.detail.show_record(None)
        self.lang_combo.blockSignals(True)
        self.lang_combo.clear()
        self.lang_combo.addItem("All languages")
        self.lang_combo.blockSignals(False)
        self.logmsg("=== Scan: %s" % src)
        w = ScanWorker(src, out, self.online_cb.isChecked(), self)
        w.progress.connect(self._on_progress)
        w.records.connect(self._on_records)
        w.message.connect(self._on_message)
        w.failed.connect(self._on_failed)
        w.finished.connect(self._scan_done)
        self.worker = w
        w.start()
        self._update_buttons()

    def _on_message(self, text):
        self.status.setText(text)
        self.logmsg(text)

    def _on_progress(self, done, total, name):
        if total:
            self.progress.setMaximum(total)
            self.progress.setValue(done)
        else:
            self.progress.setMaximum(0)
        if name:
            self.status.setText(name if len(name) < 70 else "…" + name[-68:])

    def _on_records(self, recs):
        organizer.plan(recs, self.settings)
        # plan() per batch can't see names taken by other batches; final
        # re-plan in _scan_done resolves any collisions across batches.
        self.model.add_records(recs)
        for r in recs:
            if r.notes:
                self.logmsg("%s: %s" % (r.rel_path, "; ".join(r.notes)))
        self._update_summary()

    def _scan_done(self):
        self._replan()
        self._update_summary()
        self._refresh_lang_filter()
        n = len(self.model.records)
        self.status.setText("Analyzed %d items. Review the table, then click “Arrange files”." % n)
        self.logmsg("Scan finished: %d items." % n)
        self.worker = None
        self._update_buttons()

    def _refresh_lang_filter(self):
        langs = sorted({r.language_name for r in self.model.records if r.language_name})
        cur = self.lang_combo.currentText()
        self.lang_combo.blockSignals(True)
        self.lang_combo.clear()
        self.lang_combo.addItems(["All languages"] + langs)
        idx = self.lang_combo.findText(cur)
        self.lang_combo.setCurrentIndex(max(idx, 0))
        self.lang_combo.blockSignals(False)

    def _update_summary(self):
        recs = self.model.records
        cats = Counter(r.category for r in recs)
        types = Counter(r.doc_type for r in recs if r.doc_type)
        langs = Counter(r.language_name for r in recs)
        parts = ["%d items" % len(recs)]
        parts.append(", ".join("%s %d" % kv for kv in cats.most_common()))
        if types:
            parts.append("Docs: " + ", ".join("%s %d" % kv for kv in types.most_common(4)))
        if langs:
            parts.append("Lang: " + ", ".join("%s %d" % kv for kv in langs.most_common(4)))
        self.summary.setText("  |  ".join(parts))

    def _on_failed(self, tb):
        self.logmsg(tb)
        self.tabs.setCurrentWidget(self.log)
        QMessageBox.critical(self, APP, "An unexpected error occurred. See the Log tab for details.")

    def start_arrange(self):
        self._replan()
        todo = [r for r in self.model.records if r.destination and r.status not in ("Done", "Skipped")]
        if not todo:
            QMessageBox.information(self, APP, "Nothing left to arrange.")
            return
        out = self.settings.output_root
        verb = "MOVE" if self.settings.mode == "move" else "copy"
        msg = ("%s %d items into:\n%s\n\nA log is saved so this can be undone with "
               "“Undo last arrangement”." % (verb.capitalize() if verb == "copy" else verb, len(todo), out))
        icon = QMessageBox.Warning if self.settings.mode == "move" else QMessageBox.Question
        box = QMessageBox(icon, APP, msg, QMessageBox.Ok | QMessageBox.Cancel, self)
        if box.exec_() != QMessageBox.Ok:
            return
        self._save_settings()
        self.logmsg("=== Arrange (%s) -> %s" % (self.settings.mode, out))
        w = ArrangeWorker(self.model.records, self.settings, self)
        w.progress.connect(self._on_progress)
        w.finished_ok.connect(self._arrange_done)
        w.failed.connect(self._on_failed)
        w.finished.connect(self._worker_finished)
        self.worker = w
        w.start()
        self._update_buttons()

    def _arrange_done(self, log_path):
        recs = self.model.records
        done = sum(r.status == "Done" for r in recs)
        errors = [r for r in recs if r.status == "Error"]
        self.model.refresh()
        for r in errors:
            self.logmsg("ERROR %s: %s" % (r.path, "; ".join(r.notes[-1:])))
        self.logmsg("Arranged %d items. Catalog: %s. Run log: %s" % (
            done, os.path.join(self.settings.output_root, organizer.CATALOG_NAME), log_path))
        text = "Arranged %d items into\n%s" % (done, self.settings.output_root)
        if errors:
            text += "\n\n%d items failed; see the Log tab." % len(errors)
        box = QMessageBox(QMessageBox.Information, APP, text, QMessageBox.Ok, self)
        open_btn = box.addButton("Open output folder", QMessageBox.ActionRole)
        box.exec_()
        if box.clickedButton() is open_btn:
            open_path(self.settings.output_root)

    def _worker_finished(self):
        self.worker = None
        self.progress.setMaximum(1)
        self.progress.setValue(1)
        self._update_buttons()

    def start_undo(self):
        out = self._output_root()
        log_path = organizer.latest_log(out) if out else None
        if not log_path:
            QMessageBox.information(self, APP, "No arrangement log found in\n%s" % (out or "(no output folder)"))
            return
        import json
        with open(log_path, encoding="utf-8") as fh:
            info = json.load(fh)
        n = len(info.get("entries", []))
        what = ("move %d items back to their original folders" if info.get("mode") == "move"
                else "delete the %d copies made in the output folder (originals are untouched)") % n
        if QMessageBox.question(self, APP, "Undo the arrangement from %s?\n\nThis will %s." % (
                info.get("created", "?"), what)) != QMessageBox.Yes:
            return
        w = UndoWorker(log_path, self)
        w.progress.connect(self._on_progress)
        w.finished_ok.connect(self._undo_done)
        w.failed.connect(self._on_failed)
        w.finished.connect(self._worker_finished)
        self.worker = w
        w.start()
        self._update_buttons()

    def _undo_done(self, problems):
        for p in problems:
            self.logmsg("UNDO: " + p)
        for r in self.model.records:
            if r.status == "Done":
                r.status = "Analyzed"
        self.model.refresh()
        msg = "Undo complete." if not problems else \
            "Undo finished with %d problems; see the Log tab." % len(problems)
        QMessageBox.information(self, APP, msg + "\nRun “Scan” again if files were moved back.")

    def stop_worker(self):
        if self._busy() and hasattr(self.worker, "stop"):
            self.worker.stop()
            self.status.setText("Stopping…")

    def export_catalog(self):
        if not self.model.records:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export catalog", os.path.join(self._output_root() or "", "catalog_export.csv"),
            "CSV (Excel) (*.csv);;JSON (*.json)")
        if path:
            organizer.export_catalog(path, self.model.records)
            self.logmsg("Catalog exported to " + path)

    # ----- selection / edits ----------------------------------------------------------
    def _selected_record(self, proxy_index):
        if not proxy_index.isValid():
            return None
        src = self.proxy.mapToSource(proxy_index)
        return self.model.records[src.row()]

    def _on_row(self, current, _prev):
        self.detail.show_record(self._selected_record(current))
        if self.tabs.currentIndex() != 0 and self.detail.rec:
            self.tabs.setCurrentIndex(0)

    def _on_double_click(self, index):
        rec = self._selected_record(index)
        if rec:
            open_path(rec.destination if rec.status == "Done" else rec.path)

    def _on_record_edited(self, rec: FileRecord):
        self._replan()
        self.model.refresh_record(rec)
        self._refresh_lang_filter()
        self._update_summary()
        self.detail.show_record(rec)
        self.status.setText("Saved changes for %s" % rec.name)

    def _about(self):
        QMessageBox.about(self, APP, "<b>%s %s</b><p>Arranges documents, images, videos and audio "
                          "by type, document type and language, and extracts bibliographic "
                          "metadata into a catalog.</p>" % (APP, __version__))

    def closeEvent(self, e):
        if self._busy():
            if QMessageBox.question(self, APP, "A task is still running. Stop it and quit?") != QMessageBox.Yes:
                e.ignore()
                return
            if hasattr(self.worker, "stop"):
                self.worker.stop()
            self.worker.wait(5000)
        self._save_settings()
        e.accept()
