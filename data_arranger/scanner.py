"""Walk the input folder and produce FileRecords.

Some things must be moved as a unit rather than file by file:
  * a folder of Chaoxing .pdg page images is one book
  * a saved web page "page.html" with its "page_files" folder
"""
import os
from typing import Callable, Iterator, Optional

from .formats import identify
from .models import CAT_DOCUMENT, FileRecord

SKIP_FILES = {"thumbs.db", "desktop.ini", ".ds_store", "folder.jpg", "albumartsmall.jpg"}
SKIP_DIRS = {"$recycle.bin", "system volume information", ".git", "__pycache__", ".svn"}
COMPANION_SUFFIXES = ("_files", ".files", "-files", "_archivos", "-Dateien", "_fichiers")
PDG_MIN = 3


def _is_pdg_book(dirpath: str, files) -> bool:
    pdg = [f for f in files if f.lower().endswith(".pdg")]
    return len(pdg) >= PDG_MIN and len(pdg) >= 0.5 * len(files)


def _folder_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def scan(root: str, exclude: Optional[str] = None,
         cancelled: Callable[[], bool] = lambda: False) -> Iterator[FileRecord]:
    root = os.path.abspath(root)
    exclude = os.path.normcase(os.path.abspath(exclude)) if exclude else None

    for dirpath, dirnames, filenames in os.walk(root):
        if cancelled():
            return
        # Never descend into the output folder (it may sit inside the input).
        dirnames[:] = sorted(
            d for d in dirnames
            if d.lower() not in SKIP_DIRS and not d.startswith(".")
            and (exclude is None or os.path.normcase(os.path.join(dirpath, d)) != exclude))

        if dirpath != root and _is_pdg_book(dirpath, filenames):
            dirnames[:] = []
            yield FileRecord(
                path=dirpath, rel_path=os.path.relpath(dirpath, root), ext="pdg",
                category=CAT_DOCUMENT, format="PDG book (folder)", is_bundle=True,
                size=_folder_size(dirpath))
            continue

        lower_dirs = {d.lower(): d for d in dirnames}
        claimed = set()
        for fname in sorted(filenames):
            if cancelled():
                return
            if fname.lower() in SKIP_FILES or fname.startswith("~$") or fname.startswith("._"):
                continue
            path = os.path.join(dirpath, fname)
            if not os.path.isfile(path):
                continue
            ext, category, label = identify(path)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            rec = FileRecord(path=path, rel_path=os.path.relpath(path, root), ext=ext,
                             category=category, format=label, size=size)
            if ext in ("html", "htm", "xhtml", "shtml"):
                stem = os.path.splitext(fname)[0]
                for suffix in COMPANION_SUFFIXES:
                    d = lower_dirs.get((stem + suffix).lower())
                    if d and d not in claimed:
                        rec.companions.append(os.path.join(dirpath, d))
                        claimed.add(d)
            yield rec
        dirnames[:] = [d for d in dirnames if d not in claimed]
