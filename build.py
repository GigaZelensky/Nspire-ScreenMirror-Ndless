"""Build the standalone receiver against the existing, optimized player sources."""

from pathlib import Path
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import shutil
import subprocess
import sys
from tools.build_config import PLAYER, find_sdk, xvid_sources

ROOT = Path(__file__).resolve().parent
REPO = PLAYER


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", type=Path)
    parser.add_argument(
        "--direct-vram",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Verified CX II direct H.264 display output (enabled by default)",
    )
    parser.add_argument(
        "--decode-guard",
        action="store_true",
        help="Diagnostic only: check pending H.264 allocations across native scheduler calls",
    )
    parser.add_argument(
        "--capture-decode-input",
        action="store_true",
        help="Diagnostic only: keep original H.264 input in RAM and save it on decode failure",
    )
    parser.add_argument(
        "--idle-timing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Retain verified native CX II memory timing during explicit overclock (enabled by default)",
    )
    parser.add_argument(
        "--diagnostic-logging",
        action="store_true",
        help="Developer build: automatically save receiver checkpoints; normal builds use Ctrl+D",
    )
    parser.add_argument(
        "--sram-tables",
        action="store_true",
        help="Experimental temporary CX II colour-table SRAM loan",
    )
    parser.add_argument(
        "--test-native-disconnect",
        action="store_true",
        help="Hardware test only: allow native disconnect cleanup on connected exit",
    )
    parser.add_argument(
        "--main-source",
        type=Path,
        default=ROOT / "calc/main.c",
        help="Alternate standalone entry point for hardware benchmarks",
    )
    parser.add_argument(
        "--sdl-directory",
        type=Path,
        help="Directory with include/SDL and lib/libSDL.a; defaults to the SDK",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "build")
    args = parser.parse_args()
    sdk = find_sdk(args.sdk)
    sdl = args.sdl_directory.resolve() if args.sdl_directory else sdk
    if not (sdl / "lib/libSDL.a").is_file() or not (sdl / "include/SDL/SDL.h").is_file():
        parser.error(
            "nSDL is missing. Run build_sdl.py --sdk PATH, then pass --sdl-directory build/nsdl."
        )
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    cc = shutil.which("arm-none-eabi-gcc")
    if not cc:
        raise RuntimeError("ARM GNU compiler not found")
    flags = [
        "-std=c99",
        "-marm",
        "-mcpu=arm926ej-s",
        "-mtune=arm926ej-s",
        "-mfloat-abi=soft",
        "-D_TINSPIRE",
        "-DNDEBUG",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-misleading-indentation",
        "-ffunction-sections",
        "-fdata-sections",
        "-fno-strict-aliasing",
        "-fomit-frame-pointer",
        "-flto",
        "-isystem",
        str(sdl / "include"),
        "-isystem",
        str(sdk / "include"),
        "-I",
        str(ROOT / "calc"),
        "-I",
        str(REPO / "src"),
        "-I",
        str(REPO / "src/codecs"),
        "-I",
        str(REPO / "src/codecs/h264bsd"),
        "-I",
        str(REPO / "src/player"),
        "-I",
        str(REPO / "src/platform"),
    ]
    xvid = xvid_sources()
    if args.direct_vram:
        flags.append("-DMIRROR_DIRECT_VRAM=1")
    if args.decode_guard:
        flags.append("-DMIRROR_DECODE_GUARD=1")
    if args.decode_guard or args.capture_decode_input:
        flags.append("-DMIRROR_CAPTURE_INPUT=1")
    if args.diagnostic_logging or args.decode_guard or args.capture_decode_input:
        flags.append("-DMIRROR_DIAGNOSTIC_AUTOSAVE=1")
    if args.idle_timing:
        flags.append("-DMIRROR_IDLE_TIMING=1")
    if args.sram_tables:
        flags.append("-DMIRROR_SRAM_TABLES=1")
    if args.test_native_disconnect:
        flags.append("-DMIRROR_FORCE_NATIVE_ABORT=1")
    sources = [
        REPO / "src/codecs/mpeg4_xvid.c",
        *xvid,
        args.main_source.resolve(),
        ROOT / "calc/input.c",
        ROOT / "calc/profile.c",
        ROOT / "calc/decoder.c",
        ROOT / "calc/sram_tables.c",
        ROOT / "calc/pixels.c",
        REPO / "src/codecs/rgb565_arm.S",
        REPO / "src/initfini.c",
        REPO / "src/player/native_interrupts.c",
        REPO / "src/player/native_usb_ui.c",
        REPO / "src/player/native_usb_ui_resolver.c",
        REPO / "src/player/performance_clock.c",
        REPO / "src/platform/portable_reader_platform.c",
        REPO / "src/platform/nspire_hardware.c",
        *sorted(p for p in (REPO / "src/codecs/h264bsd").rglob("*") if p.suffix in (".c", ".S")),
    ]

    def compile_source(source):
        obj = out / (
            source.stem + "-" + hashlib.sha256(str(source).encode()).hexdigest()[:8] + ".o"
        )
        xvid_source = "xvid" in source.parts or source.name == "mpeg4_xvid.c"
        hot = source.parent.name == "h264bsd" or source.name == "decoder.c" or xvid_source
        codec_warning_flags = (
            ["-Wno-error"] if source.parent.name == "h264bsd" or xvid_source else []
        )
        if xvid_source:
            codec_warning_flags += [
                "-I" + str(REPO / "src/codecs/xvid"),
                "-DARCH_IS_32BIT",
                "-DARCH_IS_ARM",
                "-DXVID_DECODER_ONLY",
                "-DNDVIDEO_XVID_RGB565_ONLY",
                "-DNDVIDEO_XVID_NO_POSTPROC",
            ]
        if source.name == "mpeg4_xvid.c":
            codec_warning_flags = ["-I" + str(REPO / "src/codecs/xvid"), *codec_warning_flags]
        command = [
            cc,
            *(["-I" + str(REPO / "src/codecs/xvid")] if xvid_source else []),
            *flags,
            *codec_warning_flags,
            *(
                ["-O3", "-funroll-loops", "-falign-functions=32", "-falign-loops=32"]
                if hot
                else ["-Os"]
            ),
            "-c",
            "-MMD",
            "-MF",
            str(obj.with_suffix(".d")),
            str(source),
            "-o",
            str(obj),
        ]
        if source.suffix == ".S":
            command = [
                cc,
                "-marm",
                "-mcpu=arm926ej-s",
                "-MMD",
                "-MF",
                str(obj.with_suffix(".d")),
                "-c",
                str(source),
                "-o",
                str(obj),
            ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(str(source) + "\n" + result.stdout + result.stderr)
        return str(obj)

    with ThreadPoolExecutor(max_workers=4) as pool:
        objects = list(pool.map(compile_source, sources))
    elf = out / "ndmirror.elf"
    subprocess.run(
        [
            cc,
            "-marm",
            "-mcpu=arm926ej-s",
            "-mfloat-abi=soft",
            "-nostdlib",
            "-flto",
            "-O3",
            "-Wl,--gc-sections,--pic-veneer,--emit-relocs,--wrap=SDL_StartTicks,--wrap=SDL_GetTicks",
            "-Wl,-T," + str(sdk / "system/ldscript"),
            str(sdk / "system/crt0.o"),
            str(sdk / "system/crti.o"),
            *objects,
            "-L" + str(sdk / "lib"),
            "-Wl,--start-group",
            str(sdl / "lib/libSDL.a"),
            "-lndls",
            "-lsyscalls",
            "-lm",
            "-lc",
            "-lgcc",
            "-Wl,--end-group",
            str(sdk / "system/crtn.o"),
            "-o",
            str(elf),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(REPO / "tools/pack_zehn.py"),
            "--input",
            str(elf),
            "--output",
            str(out / "ndmirror.tns"),
            "--zehn-output",
            str(out / "ndmirror.zehn"),
            "--loader",
            str(sdk / "tools/zehn_loader/zehn_loader.tns"),
            "--name",
            "ND Screen Mirror",
            "--author",
            "GigaZelensky",
            "--version",
            "1",
            "--ndless-min",
            "45",
            "--hww-support",
            "--uses-lcd-blit",
            "--no-support-32mb",
        ],
        check=True,
    )
    print(out / "ndmirror.tns")


if __name__ == "__main__":
    main()
