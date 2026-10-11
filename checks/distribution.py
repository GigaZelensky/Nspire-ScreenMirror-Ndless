"""Releases exclude developer data and reject stale/mismatched inputs."""

from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# Packaging needs this generated input even in a fresh source checkout.
if not (ROOT / "drivers/virtual-display/ND-Display-Setup.exe").is_file():
    subprocess.run([sys.executable, str(ROOT / "build_display_setup.py")], cwd=ROOT, check=True)
with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
    base = Path(temporary)
    calculator, pc = base / "calculator", base / "pc"
    calculator.mkdir()
    (pc / "_internal").mkdir(parents=True)
    for name in ("ndmirror.tns", "ndmirror.elf", "ndmirror.zehn"):
        (calculator / name).write_bytes(b"synthetic build input")
    (pc / "ND-Screen-Mirror.exe").write_bytes(b"synthetic sender")
    (pc / "_internal/runtime.txt").write_text("runtime fixture")
    (pc / "private-recording.mp4").write_bytes(b"must not be published")
    destination = base / "ND-Screen-Mirror-0.1.0-windows"
    command = [
        sys.executable,
        str(ROOT / "package.py"),
        str(destination),
        "--calculator-build",
        str(calculator),
        "--pc-build",
        str(pc),
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not (destination / "PC/private-recording.mp4").exists()
    info = json.loads((destination / "build.json").read_text())
    assert info["physical_usb_tested"] is False
    assert (destination / "source/nd-screen-mirror/vendor/ndvideo/sources.json").is_file()
    assert (destination / "source/nd-screen-mirror/build_sdl.py").is_file()
    with zipfile.ZipFile(destination.with_name(destination.name + ".zip")) as archive:
        assert archive.testzip() is None
        assert not any(
            "__pycache__" in name or name.endswith((".mp4", ".jsonl"))
            for name in archive.namelist()
        )
    for line in (destination / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split("  ", 1)
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == expected
    assert subprocess.run(command, cwd=ROOT, capture_output=True).returncode != 0
    validation = base / "hardware.json"
    validation.write_text(
        json.dumps({"receiver_sha256": "wrong", "new_receiver_hardware_tested": True})
    )
    wrong = [*command]
    wrong[2] = str(base / "different-release")
    assert (
        subprocess.run(
            [*wrong, "--hardware-validation", str(validation)], cwd=ROOT, capture_output=True
        ).returncode
        != 0
    )
    (pc / "_internal/navnet.dll").write_bytes(b"proprietary fixture")
    assert subprocess.run(wrong, cwd=ROOT, capture_output=True).returncode != 0
print(
    "PASS clean source/package contents, hashes/ZIP, absent hardware evidence, stale destination and mismatched/proprietary rejection"
)
