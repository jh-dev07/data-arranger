"""Plan destinations, copy/move files, write the catalog, and undo."""
import csv
import datetime
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

from .models import CAT_DOCUMENT, CAT_OTHER, CATALOG_FIELDS, FileRecord

LOG_DIR = "_arranger_logs"
CATALOG_NAME = "catalog.csv"

PLACEHOLDERS = ["category", "doctype", "language", "lang", "format", "ext",
                "year", "title", "authors", "journal", "publisher", "source_folder"]


@dataclass
class Settings:
    output_root: str = ""
    mode: str = "copy"                       # "copy" or "move"
    doc_template: str = "{category}/{doctype}/{language}"
    media_template: str = "{category}/{language}"
    other_template: str = "{category}/{ext}"
    rename: bool = False
    rename_template: str = "{year} - {authors} - {title}"
    remove_empty_dirs: bool = True


_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul"} | {"com%d" % i for i in range(1, 10)} | {"lpt%d" % i for i in range(1, 10)}


def sanitize(component: str, limit: int = 100) -> str:
    s = _INVALID.sub("_", component or "")
    s = " ".join(s.split()).strip(" .")
    if len(s) > limit:
        s = s[:limit].rstrip(" .")
    if s.lower().split(".")[0] in _RESERVED:
        s = "_" + s
    return s


def _fields(rec: FileRecord) -> Dict[str, str]:
    if rec.authors:
        first = rec.authors[0]
        authors = first + (" et al." if len(rec.authors) > 2 else
                           (", " + rec.authors[1] if len(rec.authors) == 2 else ""))
    else:
        authors = ""
    src_folder = os.path.dirname(rec.rel_path) if not rec.is_bundle else os.path.dirname(rec.rel_path.rstrip("\\/"))
    return {
        "category": rec.category,
        "doctype": rec.doc_type or ("Unclassified" if rec.category == CAT_DOCUMENT else ""),
        "language": rec.language_name or "Unknown",
        "lang": rec.language or "unknown",
        "format": rec.format,
        "ext": (rec.ext or "no-extension").upper(),
        "year": rec.year,
        "title": rec.title,
        "authors": authors,
        "journal": rec.journal or rec.conference,
        "publisher": rec.publisher,
        "source_folder": src_folder.replace("\\", "/"),
    }


def render_template(template: str, rec: FileRecord) -> List[str]:
    """Render a folder template into sanitized path components."""
    values = _fields(rec)

    def repl(m):
        return values.get(m.group(1), m.group(0))
    rendered = re.sub(r"\{(\w+)\}", repl, template)
    parts = []
    for part in re.split(r"[\\/]+", rendered):
        part = sanitize(part)
        if part:
            parts.append(part)
    return parts


def new_filename(rec: FileRecord, settings: Settings) -> str:
    original = rec.name
    if not settings.rename or rec.is_bundle or rec.companions or not rec.title:
        return original
    values = _fields(rec)
    name = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), ""), settings.rename_template)
    # Tidy separators left behind by empty fields, e.g. " -  - Title"
    name = re.sub(r"(\s*[-_]\s*){2,}", " - ", name)
    name = re.sub(r"^\s*[-_]\s*|\s*[-_]\s*$", "", name)
    name = re.sub(r"\(\s*\)|\[\s*\]", "", name)
    name = sanitize(name, 150)
    if not name:
        return original
    ext = os.path.splitext(original)[1]
    return name + ext


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _unique(path: str, taken: set) -> str:
    base, ext = os.path.splitext(path)
    candidate, n = path, 2
    while _key(candidate) in taken or os.path.lexists(_lp(candidate)):
        candidate = "%s (%d)%s" % (base, n, ext)
        n += 1
    return candidate


def plan(records: Iterable[FileRecord], settings: Settings) -> None:
    """Assign rec.destination for every record (no file is touched)."""
    taken = set()
    for rec in records:
        if rec.status in ("Skipped",):
            continue
        if rec.category == CAT_DOCUMENT:
            template = settings.doc_template
        elif rec.category == CAT_OTHER:
            template = settings.other_template
        else:
            template = settings.media_template
        folder = os.path.join(settings.output_root, *render_template(template, rec))
        dest = _unique(os.path.join(folder, new_filename(rec, settings)), taken)
        taken.add(_key(dest))
        for comp in rec.companions:
            taken.add(_key(os.path.join(folder, os.path.basename(comp))))
        rec.destination = dest


def _lp(path: str) -> str:
    """Use the \\\\?\\ prefix on Windows so paths over 260 chars work."""
    if sys.platform == "win32":
        path = os.path.abspath(path)
        if len(path) > 240 and not path.startswith("\\\\?\\"):
            return "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path
    return path


def _transfer(src: str, dest: str, move: bool) -> None:
    os.makedirs(_lp(os.path.dirname(dest)), exist_ok=True)
    if move:
        shutil.move(_lp(src), _lp(dest))
    elif os.path.isdir(_lp(src)):
        shutil.copytree(_lp(src), _lp(dest))
    else:
        shutil.copy2(_lp(src), _lp(dest))


def same_location(a: str, b: str) -> bool:
    return _key(a) == _key(b)


def execute(records: List[FileRecord], settings: Settings,
            progress: Callable[[int, int, str], None] = lambda *a: None,
            cancelled: Callable[[], bool] = lambda: False) -> str:
    """Copy or move every planned record. Returns the path of the run log."""
    move = settings.mode == "move"
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join(settings.output_root, LOG_DIR)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "arrange_%s.json" % stamp)
    entries = []
    touched_dirs = set()
    todo = [r for r in records if r.destination and r.status not in ("Skipped", "Done")]
    try:
        for i, rec in enumerate(todo):
            if cancelled():
                break
            progress(i, len(todo), rec.name)
            if same_location(rec.path, rec.destination):
                rec.status = "Done"
                continue
            if os.path.lexists(_lp(rec.destination)):
                # Something appeared since planning: pick a fresh name.
                rec.destination = _unique(rec.destination, set())
            try:
                _transfer(rec.path, rec.destination, move)
                entries.append({"src": rec.path, "dest": rec.destination,
                                "bundle": rec.is_bundle})
                for comp in rec.companions:
                    cdest = os.path.join(os.path.dirname(rec.destination), os.path.basename(comp))
                    if os.path.lexists(_lp(cdest)):
                        rec.notes.append("companion folder already exists at destination: " + cdest)
                        continue
                    _transfer(comp, cdest, move)
                    entries.append({"src": comp, "dest": cdest, "bundle": True})
                rec.status = "Done"
                touched_dirs.add(os.path.dirname(rec.path.rstrip("\\/")))
            except Exception as exc:
                rec.status = "Error"
                rec.notes.append("%s failed: %s" % ("move" if move else "copy", exc))
        progress(len(todo), len(todo), "")
    finally:
        with open(log_path, "w", encoding="utf-8") as fh:
            json.dump({"mode": settings.mode, "created": stamp,
                       "output_root": settings.output_root, "entries": entries},
                      fh, ensure_ascii=False, indent=1)
    if move and settings.remove_empty_dirs:
        for d in sorted(touched_dirs, key=len, reverse=True):
            _remove_empty_upwards(d)
    write_catalog(settings.output_root, [r for r in records if r.status == "Done"])
    return log_path


def _remove_empty_upwards(d: str, stop: Optional[str] = None) -> None:
    while d and os.path.isdir(d) and not os.listdir(d):
        if stop and same_location(d, stop):
            return
        try:
            os.rmdir(d)
        except OSError:
            return
        parent = os.path.dirname(d)
        if parent == d:
            return
        d = parent


def latest_log(output_root: str) -> Optional[str]:
    d = os.path.join(output_root, LOG_DIR)
    if not os.path.isdir(d):
        return None
    logs = sorted(f for f in os.listdir(d) if re.fullmatch(r"arrange_\d{8}_\d{6}\.json", f))
    return os.path.join(d, logs[-1]) if logs else None


def undo(log_path: str, progress: Callable[[int, int, str], None] = lambda *a: None) -> List[str]:
    """Reverse a run. Returns a list of problems (empty if all went well)."""
    with open(log_path, encoding="utf-8") as fh:
        log = json.load(fh)
    move = log.get("mode") == "move"
    output_root = log.get("output_root", "")
    problems = []
    entries = log.get("entries", [])
    for i, e in enumerate(reversed(entries)):
        src, dest = e["src"], e["dest"]
        progress(i, len(entries), os.path.basename(dest))
        if not os.path.lexists(_lp(dest)):
            problems.append("missing (already moved or deleted): " + dest)
            continue
        try:
            if move:
                if os.path.lexists(_lp(src)):
                    problems.append("original location is occupied, left in place: " + dest)
                    continue
                os.makedirs(_lp(os.path.dirname(src)), exist_ok=True)
                shutil.move(_lp(dest), _lp(src))
            else:
                if os.path.isdir(_lp(dest)):
                    shutil.rmtree(_lp(dest))
                else:
                    os.remove(_lp(dest))
            _remove_empty_upwards(os.path.dirname(dest), stop=output_root)
        except Exception as exc:
            problems.append("%s: %s" % (dest, exc))
    progress(len(entries), len(entries), "")
    os.replace(log_path, log_path[:-5] + ".undone.json")
    _drop_from_catalog(output_root, {e["dest"] for e in entries})
    return problems


# --- catalog ------------------------------------------------------------------

def _read_catalog(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_rows(path: str, rows: List[Dict[str, str]]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:   # BOM so Excel shows CJK
        w = csv.DictWriter(fh, fieldnames=CATALOG_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def write_catalog(output_root: str, records: List[FileRecord]) -> str:
    """Merge records into <output>/catalog.csv, keyed by destination."""
    path = os.path.join(output_root, CATALOG_NAME)
    rows = {_key(r.get("destination", "")): r for r in _read_catalog(path)}
    for rec in records:
        rows[_key(rec.destination)] = rec.to_row()
    _write_rows(path, list(rows.values()))
    return path


def _drop_from_catalog(output_root: str, dests) -> None:
    path = os.path.join(output_root, CATALOG_NAME)
    if not os.path.exists(path):
        return
    keys = {_key(d) for d in dests}
    rows = [r for r in _read_catalog(path) if _key(r.get("destination", "")) not in keys]
    _write_rows(path, rows)


def export_catalog(path: str, records: List[FileRecord]) -> None:
    if path.lower().endswith(".json"):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([r.to_row() for r in records], fh, ensure_ascii=False, indent=1)
    else:
        _write_rows(path, [r.to_row() for r in records])
