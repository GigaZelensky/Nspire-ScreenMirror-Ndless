"""Freeze the Windows sender with the tested dependency set."""

from pathlib import Path
import argparse
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/pc")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Build the Windows executable on Windows; Linux uses the Python sender.")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--onedir",
            "--windowed",
            "--name",
            "ND-Screen-Mirror",
            "--manifest",
            str(ROOT / "pc/mirror.manifest"),
            "--distpath",
            str(args.output.resolve()),
            "--workpath",
            str(ROOT / "build/pyinstaller"),
            "--specpath",
            str(ROOT / "build"),
            "--collect-all",
            "av",
            "--collect-all",
            "dxcam",
            "--collect-all",
            "lz4",
            "--add-data",
            str(ROOT / "VERSION") + os.pathsep + ".",
            "--exclude-module",
            "cv2",
            "--exclude-module",
            "PIL.AvifImagePlugin",
            "--exclude-module",
            "PIL.ImageQt",
            "--exclude-module",
            "PIL.ImageTk",
            str(ROOT / "pc/mirror.py"),
        ],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
