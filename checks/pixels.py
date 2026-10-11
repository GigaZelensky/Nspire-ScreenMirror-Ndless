from pathlib import Path
import ctypes as c
import json
import shutil
import subprocess
import sys
import numpy as np
import lz4.block

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc"))
from encoder import test_image
from pixels import PixelEncoder
from wire import PIXEL_LIMIT


def main():
    out = ROOT / "build/checks"
    out.mkdir(parents=True, exist_ok=True)
    wrapper = out / "pixel_wrapper.c"
    wrapper.write_text(
        '#include "pixels.h"\nstatic MirrorFrame f;\nint decode(const unsigned char*p,unsigned n,unsigned flags,unsigned short*out){if(n>MIRROR_FRAME)return 0;memcpy(f.data,p,n);f.total=n;f.flags=flags;return mirror_pixels_decode(&f,out,320);}\n'
    )
    dll = out / "pixels.dll"
    subprocess.run(
        [
            shutil.which("gcc"),
            "-std=c99",
            "-O3",
            "-shared",
            "-I" + str(ROOT / "calc"),
            str(wrapper),
            str(ROOT / "calc/pixels.c"),
            "-o",
            str(dll),
        ],
        check=True,
    )
    lib = c.CDLL(str(dll))
    decode = lib.decode
    decode.argtypes = [c.c_void_p, c.c_uint, c.c_uint, c.c_void_p]
    decode.restype = c.c_int
    lib.mirror_lz4_decode.argtypes = [
        c.c_void_p,
        c.c_uint,
        c.c_void_p,
        c.c_uint,
        c.POINTER(c.c_uint),
    ]
    pixels = np.zeros((240, 320), dtype="<u2")
    codec = PixelEncoder()
    sizes = []
    for i in range(100):
        image = test_image(i)
        data, key, flags = codec.encode(image)
        assert (
            len(data) <= PIXEL_LIMIT
            and decode(c.create_string_buffer(data), len(data), flags, pixels.ctypes.data) == 1
        )
        assert np.array_equal(pixels, codec.previous)
        sizes.append(len(data))
    # No-change update and malformed rectangle list must leave the reference intact.
    data, key, flags = codec.encode(test_image(99))
    assert len(lz4.block.decompress(data)) == 4
    assert decode(c.create_string_buffer(data), len(data), flags, pixels.ctypes.data) == 1
    before = pixels.copy()
    bad = b"\x01\x00\x00\x00" + b"\xff\x7f" * 4
    assert decode(
        c.create_string_buffer(bad), len(bad), 32, pixels.ctypes.data
    ) == 0 and np.array_equal(pixels, before)
    rng = np.random.default_rng(73)
    for n in [1, 4, 31, 255, 256, 4096, 153600]:
        for source in [
            bytes([11]) * n,
            bytes(range(256)) * (n // 256) + bytes(range(n % 256)),
            rng.integers(0, 256, n, dtype=np.uint8).tobytes(),
        ]:
            compressed = lz4.block.compress(source, store_size=False)
            dest = c.create_string_buffer(n + 32)
            c.memset(dest, 0x55, n + 32)
            written = c.c_uint()
            assert (
                lib.mirror_lz4_decode(
                    c.create_string_buffer(compressed), len(compressed), dest, n, c.byref(written)
                )
                == 1
            )
            assert (
                written.value == n and dest.raw[:n] == source and dest.raw[n:] == bytes([0x55]) * 32
            )
    for _ in range(3000):
        bad = rng.integers(0, 256, int(rng.integers(0, 200)), dtype=np.uint8).tobytes()
        dest = c.create_string_buffer(96)
        c.memset(dest, 0x55, 96)
        written = c.c_uint()
        lib.mirror_lz4_decode(c.create_string_buffer(bad), len(bad), dest, 64, c.byref(written))
        assert dest.raw[64:] == bytes([0x55]) * 32
    print(
        "PASS 100 lossless pixel frames match RGB565 exactly; merged/no-change rectangles; LZ4 interop and 3000 malformed bounded blocks"
    )
    (out / "pixels-results.json").write_text(
        json.dumps(
            {"frames": 100, "average_bytes": sum(sizes) / len(sizes), "largest_bytes": max(sizes)},
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
