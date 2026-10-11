"""Find build dependencies without relying on a developer's checkout paths."""

from pathlib import Path
import os
import shutil

ROOT = Path(__file__).resolve().parents[1]
PLAYER = ROOT / "vendor" / "ndvideo"


def find_sdk(path=None):
    candidates = []
    if path or os.environ.get("NDLESS_SDK"):
        candidates.append(Path(path or os.environ["NDLESS_SDK"]))
    compiler = shutil.which("nspire-gcc")
    if compiler:
        candidates.extend((Path(compiler).resolve().parent.parent, Path(compiler).resolve().parent))
    for candidate in candidates:
        if all(
            (candidate / entry).is_file()
            for entry in ("include/os.h", "system/ldscript", "lib/libndls.a")
        ):
            return candidate.resolve()
    raise RuntimeError("Ndless SDK not found. Pass --sdk PATH or set NDLESS_SDK to ndless-sdk.")


def xvid_sources():
    import json

    return [PLAYER / name for name in json.loads((PLAYER / "sources.json").read_text())["xvid"]]
