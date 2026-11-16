"""Run file analysis in a separate process.

Native libraries (PDF renderer, OCR runtime, llama.cpp) can hang or crash
on damaged files. Analysing in a child process means that such a file can
only cost its own analysis: the parent notices the crash or the time limit,
restarts the worker, and records the file with a basic, name-based analysis
instead. The worker keeps its models loaded between files.
"""
import multiprocessing
import os
import queue
import sys
import time
import traceback
from typing import Callable, Optional

from . import analyzer, classify, fusion, textmeta
from .models import CAT_DOCUMENT, FileRecord

DEFAULT_TIMEOUT = 600   # seconds per file (OCR + model on a slow CPU)


def _quiet_stdio():
    # A windowed .exe has no console; some libraries write progress bars.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


def _worker_main(inq, outq, options):
    _quiet_stdio()
    from . import ocr
    ocr.ENABLED = options.ocr
    while True:
        item = inq.get()
        if item is None:
            return
        job_id, rec = item
        try:
            result = analyzer.analyze(rec, options=options)
            outq.put((job_id, result, None))
        except BaseException:
            outq.put((job_id, rec, traceback.format_exc()[-1500:]))


def basic_analysis(rec: FileRecord, reason: str) -> FileRecord:
    """Name-based analysis used when content analysis failed."""
    rec.notes.append(reason)
    if rec.category == CAT_DOCUMENT:
        hints = textmeta.parse_filename(rec.path)
        rec.set_if_empty("title", hints.get("title", ""), "file name")
        rec.set_if_empty("authors", hints.get("authors", []), "file name")
        rec.set_if_empty("publish_date", hints.get("year", ""), "file name")
        analyzer._set_language(rec, "")
        rec.doc_type, rec.doc_type_confidence, rec.doc_type_reasons = classify.classify(rec, "")
        rec.review = fusion.review_reasons(rec, 0) + ["content could not be read"]
    rec.status = "Needs review" if rec.review else "Analyzed"
    return rec


class IsolatedAnalyzer:
    def __init__(self, options: analyzer.Options, timeout: int = DEFAULT_TIMEOUT):
        self.options = options
        self.timeout = timeout
        self.ctx = multiprocessing.get_context("spawn")
        self.proc = None
        self.job = 0
        self.crashes = 0          # consecutive crashes/timeouts
        self.degraded = ""
        self._start()

    def _start(self):
        self.inq = self.ctx.Queue()
        self.outq = self.ctx.Queue()
        self.proc = self.ctx.Process(target=_worker_main,
                                     args=(self.inq, self.outq, self.options), daemon=True)
        self.proc.start()

    def _restart(self, crashed: bool = True):
        if crashed:
            self.crashes += 1
            # Circuit breaker: a native library that keeps crashing on this PC
            # (e.g. CPU without AVX2) is switched off instead of failing every file.
            if self.crashes >= 2 and self.options.ml_mode != "off":
                self.options = analyzer.Options(self.options.online, "off", self.options.ocr)
                self.degraded = "ML model switched off after repeated crashes on this PC"
            elif self.crashes >= 4 and self.options.ocr:
                self.options = analyzer.Options(self.options.online, "off", False)
                self.degraded = "OCR switched off after repeated crashes on this PC"
        try:
            self.proc.kill()
        except Exception:
            pass
        self.proc.join(5)
        for q in (self.inq, self.outq):
            try:
                q.close()
                q.cancel_join_thread()
            except Exception:
                pass
        self._start()

    def analyze(self, rec: FileRecord, cancelled: Callable[[], bool] = lambda: False) -> FileRecord:
        self.job += 1
        job = self.job
        self.inq.put((job, rec))
        started = time.monotonic()
        while True:
            try:
                job_id, result, error = self.outq.get(timeout=0.5)
            except queue.Empty:
                if not self.proc.is_alive():
                    code = self.proc.exitcode
                    self._restart()
                    return basic_analysis(rec, "a library crashed while reading this file "
                                               "(exit code %s); analysed from the file name" % code)
                if time.monotonic() - started > self.timeout:
                    self._restart()
                    return basic_analysis(rec, "reading this file took longer than %d s; "
                                               "analysed from the file name" % self.timeout)
                if cancelled():
                    self._restart(crashed=False)
                    rec.status = "Pending"
                    return rec
                continue
            if job_id != job:
                continue        # stale answer from before a restart
            self.crashes = 0
            if error:
                return basic_analysis(rec, "unexpected error: " + error.strip().splitlines()[-1])
            if self.degraded and self.degraded not in result.notes:
                result.notes.append(self.degraded)
            return result

    def close(self):
        try:
            self.inq.put(None)
            self.proc.join(5)
        except Exception:
            pass
        if self.proc.is_alive():
            self.proc.kill()
