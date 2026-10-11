"""Build the vendored nSDL library without modifying the Ndless SDK."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse
import re
import shutil
import subprocess
from tools.build_config import ROOT, find_sdk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "build/nsdl")
    args = parser.parse_args()
    sdk = find_sdk(args.sdk)
    cc = shutil.which("arm-none-eabi-gcc")
    ar = shutil.which("arm-none-eabi-ar")
    if not cc or not ar:
        parser.error("ARM GNU gcc and ar must be on PATH.")
    source = ROOT / "vendor/nsdl"
    out = args.output.resolve()
    headers = out / "include/SDL"
    shutil.copytree(source / "include", headers, dirs_exist_ok=True)
    shutil.copy2(headers / "SDL_config.h.default", headers / "SDL_config.h")
    files = re.findall(
        r"src/[^\s\\]+\.c", (source / "Makefile.nsp").read_text().split("OBJECTS", 1)[0]
    )

    def compile_file(name):
        obj = out / "objects" / (name.replace("/", "_") + ".o")
        obj.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                cc,
                "-marm",
                "-mcpu=arm926ej-s",
                "-mfloat-abi=soft",
                "-D_TINSPIRE",
                "-Ofast",
                "-fno-strict-aliasing",
                "-ffunction-sections",
                "-fdata-sections",
                "-Wno-unused-parameter",
                "-I" + str(headers),
                "-isystem",
                str(sdk / "include"),
                "-c",
                "-MMD",
                "-MF",
                str(obj.with_suffix(".d")),
                str(source / name),
                "-o",
                str(obj),
            ],
            check=True,
        )
        return str(obj)

    with ThreadPoolExecutor(max_workers=4) as pool:
        objects = list(pool.map(compile_file, files))
    library = out / "lib/libSDL.a"
    library.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ar, "rcs", str(library), *objects], check=True)
    print(library)


if __name__ == "__main__":
    main()
