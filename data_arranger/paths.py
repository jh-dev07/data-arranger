"""Locations of bundled models and helper tools.

Layout (both when running from source and in the PyInstaller build):
    <app>/models/qwen2.5-1.5b-instruct-q4_k_m.gguf   metadata / doc-type model
    <app>/models/lid.176.ftz                         fastText language ID
    <app>/tools/7z/7z.exe                            CHM extraction
    <app>/tools/djvulibre/ddjvu.exe ...              DjVu rendering / text
"""
import os
import shutil
import sys

LLM_FILENAME = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
LID_FILENAME = "lid.176.ftz"


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _candidates(*parts):
    roots = [app_dir()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(meipass)
    env = os.environ.get("DATA_ARRANGER_HOME")
    if env:
        roots.insert(0, env)
    return [os.path.join(r, *parts) for r in roots]


def find_file(*parts):
    for p in _candidates(*parts):
        if os.path.exists(p):
            return p
    return None


def llm_model_path():
    override = os.environ.get("DATA_ARRANGER_LLM")
    if override and os.path.exists(override):
        return override
    p = find_file("models", LLM_FILENAME)
    if p:
        return p
    # Accept any GGUF placed in models/
    for d in _candidates("models"):
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(".gguf"):
                    return os.path.join(d, f)
    return None


def lid_model_path():
    return find_file("models", LID_FILENAME)


def tool(name: str):
    """Find a helper executable: bundled first, then PATH, then usual installs."""
    exe = name + (".exe" if sys.platform == "win32" else "")
    for sub in (("tools", name, exe), ("tools", "djvulibre", exe), ("tools", exe)):
        p = find_file(*sub)
        if p:
            return p
    p = shutil.which(name)
    if p:
        return p
    if sys.platform == "win32":
        pf = [os.environ.get("ProgramFiles", r"C:\Program Files"),
              os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
        known = {"7z": [r"7-Zip\7z.exe"], "ddjvu": [r"DjVuLibre\ddjvu.exe"],
                 "djvused": [r"DjVuLibre\djvused.exe"], "djvutxt": [r"DjVuLibre\djvutxt.exe"],
                 "hh": []}
        for base in pf:
            for rel in known.get(name, []):
                p = os.path.join(base, rel)
                if os.path.exists(p):
                    return p
        if name == "hh":
            p = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "hh.exe")
            if os.path.exists(p):
                return p
    return None


def work_dir() -> str:
    """Scratch space for temporary extraction (CHM pages etc.)."""
    import tempfile
    d = os.path.join(tempfile.gettempdir(), "DataArranger")
    os.makedirs(d, exist_ok=True)
    return d
