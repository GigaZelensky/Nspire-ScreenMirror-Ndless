"""Run offline regression checks; never connect USB or inject live PC input."""

from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CHECKS = [
    "check",
    "host",
    "pixels",
    "service",
    "channel_read",
    "worker_wait",
    "nnse_abort",
    "held_write",
    "replay_usb",
    "input",
    "input_scan",
    "linux_host",
    "wayland",
    "mpeg4",
    "mpeg4_adaptive",
    "persistent",
    "adaptive",
    "service_budget",
    "pacing",
    "long_gop",
    "display_rate",
    "display_target",
    "cache_plan",
    "sram_tables",
    "sram_ab",
    "capture",
    "latest_capture",
    "profile",
    "discovery_retry",
    "bridge_pool",
    "usb_protocol",
    "calculator_files",
    "diagnostics",
    "diagnostic_history",
    "decoder_recovery",
    "decoder_guard",
    "idle_timing",
    "settings",
    "ci",
]
WINDOWS_CHECKS = ["virtual_display", "gui", "restart", "distribution"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path)
    parser.add_argument(
        "--interactive-input",
        action="store_true",
        help="Also test real key injection into the test's own window",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "build/checks/report.json")
    args = parser.parse_args()
    env = os.environ.copy()
    if args.sdk:
        env["NDLESS_SDK"] = str(args.sdk.resolve())
    results = []
    selected = CHECKS + (WINDOWS_CHECKS if os.name == "nt" else [])
    if args.interactive_input and os.name == "nt":
        selected.append("input_windows")
    for name in selected:
        print(f"Checking {name}...", flush=True)
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, str(ROOT / "checks" / (name + ".py"))],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        skipped = "SKIP " in result.stdout
        results.append(
            dict(
                check=name,
                passed=result.returncode == 0 and not skipped,
                skipped=skipped,
                seconds=time.monotonic() - started,
            )
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "all_passed": all(r["passed"] for r in results),
                "checks": results,
                "physical_usb_tested": False,
            },
            indent=2,
        )
        + "\n"
    )
    if not all(r["passed"] for r in results):
        raise SystemExit(1)
    print(f"PASS all {len(results)} offline checks")


if __name__ == "__main__":
    main()
