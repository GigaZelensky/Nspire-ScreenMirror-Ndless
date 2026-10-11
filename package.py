"""Assemble a clean Windows release with complete project sources and licenses."""

from pathlib import Path
import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent
SOURCE_DIRS = ("calc", "pc", "checks", "tools", "vendor", "drivers", "docs", "licenses", ".github")
SOURCE_FILES = (
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES",
    "VERSION",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    ".clang-format",
    ".gitattributes",
    ".gitignore",
    "build.py",
    "build_sdl.py",
    "build_display_setup.py",
    "package.py",
    "Makefile",
)
DRIVER_FILES = (
    "MttVDD.inf",
    "MttVDD.dll",
    "mttvdd.cat",
    "LICENSE-MTT.txt",
    "provenance.json",
    "ND-Display-Setup.exe",
)


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--calculator-build", type=Path, required=True)
    parser.add_argument("--pc-build", type=Path, required=True)
    parser.add_argument("--check-report", type=Path)
    parser.add_argument("--hardware-validation", type=Path)
    args = parser.parse_args()
    destination = args.destination.resolve()
    archive = destination.with_name(destination.name + ".zip")
    if destination == ROOT or any(
        destination.is_relative_to(ROOT / folder) for folder in SOURCE_DIRS
    ):
        parser.error("Choose a release directory outside the project source directories.")
    if destination.exists() and any(destination.iterdir()):
        parser.error("The destination must be empty; refusing to mix old files into a release.")
    if archive.exists():
        parser.error("A release ZIP already exists at this destination.")
    required = [
        args.calculator_build / name for name in ("ndmirror.tns", "ndmirror.elf", "ndmirror.zehn")
    ]
    required += [args.pc_build / "ND-Screen-Mirror.exe"]
    if not (args.pc_build / "_internal").is_dir():
        parser.error("The PC build is missing its _internal runtime directory.")
    forbidden = {"navnet.dll", "bridgelink.dll", "bridgelinkserver.exe"}
    if any(
        path.name.casefold() in forbidden for path in args.pc_build.rglob("*") if path.is_file()
    ):
        parser.error(
            "The PC build contains proprietary TI runtime files; these must not be bundled."
        )
    required += [ROOT / "drivers/virtual-display" / name for name in DRIVER_FILES]
    for path in required:
        if not path.is_file():
            parser.error("Missing build input: " + str(path))
    report = json.loads(args.check_report.read_text()) if args.check_report else None
    if report is not None and not report.get("all_passed"):
        parser.error("The check report contains failed or skipped checks.")
    validation = (
        json.loads(args.hardware_validation.read_text()) if args.hardware_validation else None
    )
    receiver_hash = digest(args.calculator_build / "ndmirror.tns")
    hardware_matches = validation is not None and validation.get("receiver_sha256") == receiver_hash
    if validation is not None and not hardware_matches:
        parser.error("Hardware validation belongs to a different calculator binary.")
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("ndmirror.tns", "ndmirror.elf", "ndmirror.zehn"):
        shutil.copy2(args.calculator_build / name, destination / name)
    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES"):
        shutil.copy2(ROOT / name, destination / name)
    (destination / "PC").mkdir()
    shutil.copy2(args.pc_build / "ND-Screen-Mirror.exe", destination / "PC/ND-Screen-Mirror.exe")
    shutil.copytree(args.pc_build / "_internal", destination / "PC/_internal")
    driver = destination / "PC/drivers/virtual-display"
    driver.mkdir(parents=True)
    for name in DRIVER_FILES:
        shutil.copy2(ROOT / "drivers/virtual-display" / name, driver / name)
    source = destination / "source/nd-screen-mirror"
    source.mkdir(parents=True)
    for folder in SOURCE_DIRS:
        shutil.copytree(
            ROOT / folder,
            source / folder,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.o", "*.a", "ND-Display-Setup.exe"
            ),
        )
    for name in SOURCE_FILES:
        shutil.copy2(ROOT / name, source / name)
    license_folder = destination / "dependency-licenses"
    license_folder.mkdir()
    for name in ("av", "mss", "Pillow", "pyinstaller", "numpy", "dxcam", "comtypes", "lz4"):
        distribution = importlib.metadata.distribution(name)
        for entry in distribution.files or []:
            if not any(
                term in str(entry).lower() for term in ("license", "copying", "authors")
            ) or str(entry).endswith((".py", ".pyc")):
                continue
            original = Path(distribution.locate_file(entry))
            if original.is_file():
                target = license_folder / name / Path(str(entry)).name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, target)
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "-C", str(ROOT), "status", "--porcelain"], text=True
            ).strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit, dirty = None, True
    info = dict(
        version=(ROOT / "VERSION").read_text().strip(),
        source_commit=commit,
        source_dirty=dirty,
        calculator_sha256=receiver_hash,
        calculator_bytes=(destination / "ndmirror.tns").stat().st_size,
        offline_checks=report,
        physical_usb_tested=bool(
            hardware_matches and validation.get("new_receiver_hardware_tested")
        ),
        hardware_validation=validation,
        dependencies={
            name: importlib.metadata.version(name)
            for name in ("av", "mss", "Pillow", "numpy", "lz4", "dxcam", "comtypes", "pyinstaller")
        },
    )
    (destination / "build.json").write_text(json.dumps(info, indent=2) + "\n")
    (destination / "SHA256SUMS").write_text(
        "\n".join(
            digest(path) + "  " + path.relative_to(destination).as_posix()
            for path in sorted(destination.rglob("*"))
            if path.is_file() and path.name != "SHA256SUMS"
        )
        + "\n"
    )
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                bundle.write(path, Path(destination.name) / path.relative_to(destination))
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise RuntimeError("Release archive CRC check failed")
    print(
        json.dumps(
            dict(
                folder=str(destination),
                zip=str(archive),
                zip_MiB=archive.stat().st_size / 1048576,
                source_commit=commit,
                source_dirty=dirty,
                receiver_sha256=receiver_hash,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
