"""Diagnostic log: %TEMP%/DataArranger/app.log (shared by the app and its
worker process; each line carries the process id)."""
import logging
import logging.handlers
import os

_done = False


def log_path() -> str:
    from . import paths
    return os.path.join(paths.work_dir(), "app.log")


def setup(role: str) -> None:
    global _done
    if _done:
        return
    _done = True
    try:
        handler = logging.handlers.RotatingFileHandler(
            log_path(), maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8", delay=True)
    except Exception:
        return
    handler.setFormatter(logging.Formatter(
        "%(asctime)s " + role + "[%(process)d] %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
