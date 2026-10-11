"""Release preparation and publishing commands, with no GitHub mutation."""

from pathlib import Path
import hashlib
import subprocess
import sys
import tempfile
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ci_support
import publish_release

ci_support.verify()
with tempfile.TemporaryDirectory() as directory:
    base = Path(directory)
    package = base / "ND-Screen-Mirror-0.1.0-windows"
    package.mkdir()
    (package / "ndmirror.tns").write_bytes(b"receiver")
    with zipfile.ZipFile(package.with_name(package.name + ".zip"), "w") as archive:
        archive.writestr("README.md", "fixture")
    assets = base / "assets"
    ci_support.release_assets(package, assets)
    for line in (assets / "SHA256SUMS").read_text().splitlines():
        checksum, name = line.split("  ", 1)
        assert hashlib.sha256((assets / name).read_bytes()).hexdigest() == checksum
    calls = []

    def run(*args):
        calls.append(args)
        return "a" * 40 if args[:3] == ("git", "rev-parse", "HEAD") else ""

    with (
        patch.object(publish_release, "run", run),
        patch.object(
            publish_release.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
        ),
    ):
        publish_release.publish("v" + ci_support.version(), assets)
        assert not any(call[:2] == ("git", "push") for call in calls)
        assert any(call[:3] == ("gh", "release", "upload") for call in calls)
        assert any("--latest" in call and "--prerelease=false" in call for call in calls)
        calls.clear()
        publish_release.publish("latest", assets, rolling=True)
        assert any(call[:4] == ("git", "tag", "--force", "latest") for call in calls)
        assert any(call[:4] == ("git", "push", "origin", "refs/tags/latest") for call in calls)
        assert any("--latest" in call and "--prerelease=false" in call for call in calls)
        calls.clear()
        try:
            publish_release.publish("bad-tag", assets)
            raise AssertionError("Mismatched version published")
        except ValueError:
            pass
        assert not calls
print("PASS vendor verification, release asset hashes and mocked rolling/versioned publishing")
