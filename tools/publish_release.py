"""Publish completed CI assets; invoked only by the trusted release job."""

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

from ci_support import digest, version


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def publish(tag, assets, rolling=False):
    expected = "latest" if rolling else "v" + version()
    if tag != expected:
        raise ValueError("Release tag must match " + expected)
    zip_files = list(assets.glob("*.zip"))
    if len(zip_files) != 1 or not (assets / "ndmirror.tns").is_file():
        raise ValueError("Expected one Windows ZIP and ndmirror.tns")
    sha = run("git", "rev-parse", "HEAD")
    with tempfile.TemporaryDirectory() as folder:
        staged = Path(folder)
        zip_name = "ND-Screen-Mirror-windows.zip" if rolling else zip_files[0].name
        shutil.copy2(zip_files[0], staged / zip_name)
        shutil.copy2(assets / "ndmirror.tns", staged / "ndmirror.tns")
        (staged / "SHA256SUMS").write_text(
            "".join(digest(file) + "  " + file.name + "\n" for file in sorted(staged.iterdir())),
            encoding="utf-8",
        )
        notes = staged / "notes.md"
        notes.write_text(
            "Stream your PC screen to a TI-Nspire over USB, with touchpad and keyboard control.\n\n"
            "Extract the Windows ZIP and run PC/ND-Screen-Mirror.exe. "
            "Copy ndmirror.tns to the calculator. Source and license notices are included in the ZIP.\n\n"
            "Linux: follow the [Debian / Raspberry Pi OS setup guide]"
            "(https://github.com/GigaZelensky/Nspire-ScreenMirror-Ndless/blob/main/docs/linux.md). "
            "The same calculator file works with the Linux sender.\n\n"
            + ("Latest development build from the default branch.\n\n" if rolling else "")
            + "Commit: "
            + sha
            + "\n",
            encoding="utf-8",
        )
        if rolling:
            run("git", "tag", "--force", "latest", sha)
            run("git", "push", "origin", "refs/tags/latest", "--force")
        exists = subprocess.run(["gh", "release", "view", tag], capture_output=True).returncode == 0
        files = [str(file) for file in staged.iterdir() if file.name != "notes.md"]
        if exists:
            run("gh", "release", "upload", tag, *files, "--clobber")
            run(
                "gh",
                "release",
                "edit",
                tag,
                "--title",
                "ND Screen Mirror " + tag,
                "--notes-file",
                str(notes),
                "--prerelease=false",
                "--latest",
            )
        else:
            args = [
                "gh",
                "release",
                "create",
                tag,
                *files,
                "--target",
                sha,
                "--title",
                "ND Screen Mirror " + tag,
                "--notes-file",
                str(notes),
            ]
            args.append("--latest")
            run(*args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--rolling", action="store_true")
    args = parser.parse_args()
    publish(args.tag, args.assets, args.rolling)


if __name__ == "__main__":
    main()
