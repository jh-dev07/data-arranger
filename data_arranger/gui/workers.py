"""Background threads so the window stays responsive during long runs."""
import time
import traceback

from PyQt5.QtCore import QThread, pyqtSignal

from .. import analyzer, organizer, scanner
from ..isolation import IsolatedAnalyzer
from ..models import CAT_DOCUMENT


class ScanWorker(QThread):
    progress = pyqtSignal(int, int, str)          # done, total, current name
    records = pyqtSignal(list)                    # batch of analyzed records
    message = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, root, exclude, options, parent=None):
        super().__init__(parent)
        self.root, self.exclude, self.options = root, exclude, options
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            self.message.emit("Listing files in %s ..." % self.root)
            items = []
            for rec in scanner.scan(self.root, self.exclude, lambda: self._stop):
                items.append(rec)
                if len(items) % 500 == 0:
                    self.progress.emit(0, 0, "found %d files" % len(items))
            total = len(items)
            docs = sum(1 for r in items if r.category == CAT_DOCUMENT)
            self.message.emit("Found %d items (%d documents). Analyzing... the first document "
                              "takes longer while the OCR and ML models load." % (total, docs))
            worker = IsolatedAnalyzer(self.options)
            batch, last = [], time.monotonic()
            started = time.monotonic()
            try:
                for i, rec in enumerate(items):
                    if self._stop:
                        self.message.emit("Scan stopped by user after %d of %d items." % (i, total))
                        break
                    eta = ""
                    if i >= 3:
                        per = (time.monotonic() - started) / i
                        rem = int(per * (total - i))
                        eta = "  (~%d min left)" % (rem // 60 + 1) if rem > 60 else ""
                    self.progress.emit(i, total, rec.rel_path + eta)
                    rec = worker.analyze(rec, cancelled=lambda: self._stop)
                    items[i] = rec
                    if rec.status == "Pending":        # cancelled mid-file
                        continue
                    batch.append(rec)
                    if len(batch) >= 10 or time.monotonic() - last > 0.5:
                        self.records.emit(batch)
                        batch, last = [], time.monotonic()
            finally:
                worker.close()
            if batch:
                self.records.emit(batch)
            self.progress.emit(total, total, "")
        except Exception:
            self.failed.emit(traceback.format_exc())


class ArrangeWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished_ok = pyqtSignal(str)                 # log path
    failed = pyqtSignal(str)

    def __init__(self, records, settings, parent=None):
        super().__init__(parent)
        self.recs, self.settings = records, settings
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            log = organizer.execute(self.recs, self.settings,
                                    progress=self.progress.emit, cancelled=lambda: self._stop)
            self.finished_ok.emit(log)
        except Exception:
            self.failed.emit(traceback.format_exc())


class UndoWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished_ok = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, log_path, parent=None):
        super().__init__(parent)
        self.log_path = log_path

    def run(self):
        try:
            self.finished_ok.emit(organizer.undo(self.log_path, progress=self.progress.emit))
        except Exception:
            self.failed.emit(traceback.format_exc())
