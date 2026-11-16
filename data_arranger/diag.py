"""`DataArranger.exe --diag-llm out.txt`: load llama.cpp step by step and
record each step, to locate failures in packaged builds."""
import ctypes
import os
import sys
import traceback


def run(out_path: str) -> int:
    lines = []

    def log(msg):
        lines.append(msg)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def step(name, fn):
        log("STEP " + name)
        try:
            r = fn()
            log("  ok: %r" % (r,))
            return r
        except BaseException:
            log("  FAILED:\n" + traceback.format_exc())
            return None

    log("python %s frozen=%s exe=%s" % (sys.version, getattr(sys, "frozen", False), sys.executable))
    import llama_cpp
    libdir = os.path.join(os.path.dirname(llama_cpp.__file__), "lib")
    log("llama_cpp %s at %s" % (getattr(llama_cpp, "__version__", "?"), llama_cpp.__file__))
    log("lib dir %s: %s" % (libdir, sorted(os.listdir(libdir)) if os.path.isdir(libdir) else "MISSING"))
    exe_dir = os.path.dirname(sys.executable)
    log("exe dir dlls: %s" % sorted(f for f in os.listdir(exe_dir) if f.lower().endswith(".dll"))[:80])
    ll = llama_cpp.llama_cpp
    ggml = None
    for name in ("ggml.dll", "libggml.so", "libggml.dylib"):
        p = os.path.join(libdir, name)
        if os.path.exists(p):
            ggml = step("load " + name, lambda p=p: ctypes.CDLL(p))
    if ggml is not None:
        for fn in ("ggml_backend_reg_count", "ggml_backend_dev_count"):
            if hasattr(ggml, fn):
                f = getattr(ggml, fn)
                f.restype = ctypes.c_size_t
                step(fn, f)
    step("llama_backend_init", ll.llama_backend_init)
    if ggml is not None and hasattr(ggml, "ggml_backend_dev_count"):
        step("ggml_backend_dev_count after init", ggml.ggml_backend_dev_count)
    step("llama_print_system_info", lambda: ll.llama_print_system_info())
    from . import paths
    model = paths.llm_model_path()
    log("model %s" % model)
    params = step("llama_model_default_params", ll.llama_model_default_params)
    if params is not None and model:
        loader = getattr(ll, "llama_model_load_from_file", None) or ll.llama_load_model_from_file
        m = step("model load (low level)", lambda: loader(model.encode("utf-8"), params))
        log("  model pointer null: %s" % (not m,))
    step("Llama() high level", lambda: llama_cpp.Llama(model_path=model, n_ctx=512, verbose=True))
    return 0
