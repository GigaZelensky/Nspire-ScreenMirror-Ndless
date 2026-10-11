"""Verify vendored inputs and assemble files shared by build jobs."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def verify():
    for name in ("ndvideo", "nsdl"):
        folder = ROOT / "vendor" / name
        expected = json.loads((folder / "provenance.json").read_text())["files"]
        actual = {
            file.relative_to(folder).as_posix()
            for file in folder.rglob("*")
            if file.is_file() and file.name != "provenance.json"
        }
        if actual != set(expected):
            raise ValueError("Vendor inventory changed without provenance: " + name)
        for name, checksum in expected.items():
            path = (folder / name).resolve()
            if not path.is_relative_to(folder.resolve()) or digest(path) != checksum:
                raise ValueError("Vendor hash mismatch: " + name)
    folder = ROOT / "drivers/virtual-display"
    hashes = json.loads((folder / "provenance.json").read_text())["hashes"]
    for name, checksum in hashes.items():
        if digest(folder / name) != checksum:
            raise ValueError("Signed driver file changed: " + name)
    print("PASS selected vendor inventory, source hashes and signed driver hashes")


def version():
    value = (ROOT / "VERSION").read_text().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", value):
        raise ValueError("VERSION must contain a release version, such as 0.1.0")
    return value


def export_sdk(sdk, output):
    sdk = sdk.resolve()
    required = [
        sdk / "include/keys.h",
        sdk / "include/os.h",
        sdk / "system/ldscript",
        sdk / "lib/libndls.a",
    ]
    if not all(path.is_file() for path in required):
        raise ValueError("Build the SDK libraries before exporting the check dependency")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((sdk / "include").rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(sdk))
        for path in required[2:]:
            archive.write(path, path.relative_to(sdk))
        archive.writestr("SDK-REVISION", (ROOT / ".github/ndless-revision").read_text())


def release_assets(package, output):
    archive = package.with_name(package.name + ".zip")
    if not archive.is_file() or not (package / "ndmirror.tns").is_file():
        raise ValueError("Package ZIP and calculator receiver are required")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose an empty release asset directory")
    output.mkdir(parents=True, exist_ok=True)
    for source in (archive, package / "ndmirror.tns"):
        shutil.copy2(source, output / source.name)
    (output / "SHA256SUMS").write_text(
        "".join(
            digest(path) + "  " + path.name + "\n"
            for path in sorted(output.iterdir())
            if path.is_file()
        ),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify")
    commands.add_parser("version")
    sdk = commands.add_parser("export-sdk")
    sdk.add_argument("--sdk", type=Path, required=True)
    sdk.add_argument("--output", type=Path, required=True)
    assets = commands.add_parser("release-assets")
    assets.add_argument("--package", type=Path, required=True)
    assets.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "verify":
        verify()
    elif args.command == "version":
        print(version())
    elif args.command == "export-sdk":
        export_sdk(args.sdk, args.output)
    else:
        release_assets(args.package, args.output)


if __name__ == "__main__":
    main()
