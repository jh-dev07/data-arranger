"""Download the bundled models into ./models (used by build_windows.bat and CI)."""
import os
import sys
import urllib.request

MODELS = [
    ("qwen2.5-1.5b-instruct-q4_k_m.gguf",
     "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
     1117320736),
    ("lid.176.ftz",
     "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz",
     938013),
]


def main():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    os.makedirs(root, exist_ok=True)
    for name, url, size in MODELS:
        dest = os.path.join(root, name)
        if os.path.exists(dest) and os.path.getsize(dest) == size:
            print("ok  ", name)
            continue
        print("get ", name, "(%d MB)" % (size // 1048576))
        tmp = dest + ".part"
        urllib.request.urlretrieve(url, tmp)
        if os.path.getsize(tmp) != size:
            os.remove(tmp)
            sys.exit("download of %s is incomplete" % name)
        os.replace(tmp, dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
