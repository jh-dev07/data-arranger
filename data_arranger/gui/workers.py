"""Background threads so the window stays responsive during long runs."""
import time
import traceback

from PyQt5.QtCore import QThread, pyqtSignal

from .. import analyzer, organizer, scanner


class ScanWorker(QThread):
    progress = pyqtSignal(int, int, str)          # done, total, current name
    records = pyqtSignal(list)                    # batch of analyzed records
    message = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, root, exclude, online, parent=None):
        super().__init__(parent)
        self.root, self.exclude, self.online = root, exclude, online
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
            self.message.emit("Found %d items. Analyzing..." % total)
            batch, last = [], time.monotonic()
            for i, rec in enumerate(items):
                if self._stop:
                    self.message.emit("Scan stopped by user after %d of %d items." % (i, total))
                    break
                self.progress.emit(i, total, rec.rel_path)
                analyzer.analyze(rec, online=self.online)
                batch.append(rec)
                if len(batch) >= 25 or time.monotonic() - last > 0.3:
                    self.records.emit(batch)
                    batch, last = [], time.monotonic()
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
