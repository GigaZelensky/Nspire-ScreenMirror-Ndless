"""Build and verify the native display installer; never install a driver."""

from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
if os.name != "nt":
    raise SystemExit("Build the Windows helper on Windows with MinGW gcc and windres.")
for name in ("gcc", "windres"):
    if not shutil.which(name):
        raise SystemExit("Missing build tool: " + name)
assets = ROOT / "drivers/virtual-display"
build = ROOT / "build"
build.mkdir(exist_ok=True)
resource = build / "display-setup-resource.o"
helper = assets / "ND-Display-Setup.exe"
subprocess.run(
    ["windres", "pc/virtual_display_setup.rc", "-O", "coff", "-o", str(resource)],
    cwd=ROOT,
    check=True,
)
subprocess.run(
    [
        "gcc",
        "-municode",
        "-Os",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-misleading-indentation",
        "pc/virtual_display_setup.c",
        str(resource),
        "-o",
        str(helper),
        "-lsetupapi",
        "-lnewdev",
        "-lshell32",
        "-ladvapi32",
        "-lbcrypt",
        "-lwintrust",
        "-lcrypt32",
        "-luser32",
    ],
    cwd=ROOT,
    check=True,
)
subprocess.run([str(helper), "--verify-driver", str(assets)], check=True)
(build / "display-setup.json").write_text(
    json.dumps(
        {"file": helper.name, "sha256": hashlib.sha256(helper.read_bytes()).hexdigest()}, indent=2
    )
    + "\n"
)
print(helper)
