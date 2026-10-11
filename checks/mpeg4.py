"""Decode the live MPEG-4 encoder with the production Xvid decoder and compare FFmpeg."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import subprocess
import shutil
import sys
import json
import numpy as np
import av

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "vendor/ndvideo"
sys.path.insert(0, str(ROOT / "pc"))
from encoder import Encoder, test_image

out = ROOT / "build/checks/mpeg4" / sys.platform
out.mkdir(parents=True, exist_ok=True)
source = out / "main.c"
source.write_text(r"""
#undef NDEBUG
#include "mpeg4_xvid.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
static uint16_t pixels[320*240];
int main(int argc,char**argv){assert(argc==3);FILE*f=fopen(argv[1],"rb"),*o=fopen(argv[2],"wb");assert(f&&o);
void*handle=NULL;assert(mpeg4_xvid_global_init(NULL,0)&&mpeg4_xvid_create(&handle,320,240));
unsigned size,n=0;while(fread(&size,4,1,f)){assert(size<=65536);uint8_t*data=calloc(1,size+16);assert(data&&fread(data,1,size,f)==size);
if(!mpeg4_xvid_decode_frame(handle,data,size,pixels,320,240,true,n==0)){fprintf(stderr,"frame %u: %s\n",n,mpeg4_xvid_last_error());return 1;}
assert(fwrite(pixels,2,320*240,o)==320*240);free(data);++n;}
mpeg4_xvid_destroy(handle);fclose(f);fclose(o);printf("PASS production MPEG4 decoder: %u frames\n",n);return 0;}
""")
xvid = [REPO / name for name in json.loads((REPO / "sources.json").read_text())["xvid"]]
sources = [source, REPO / "src/codecs/mpeg4_xvid.c", *xvid]
cc = shutil.which("gcc")
flags = [
    *(["-include", "windows.h"] if sys.platform == "win32" else []),
    "-O2",
    "-std=gnu99",
    "-DNDEBUG",
    "-DARCH_IS_64BIT",
    "-DARCH_IS_GENERIC",
    "-D__unused=__attribute__((unused))",
    "-DXVID_DECODER_ONLY",
    "-DNDVIDEO_XVID_RGB565_ONLY",
    "-DNDVIDEO_XVID_NO_POSTPROC",
    "-ffunction-sections",
    "-fdata-sections",
    "-I" + str(REPO / "src/codecs/xvid"),
    "-I" + str(REPO / "src/codecs"),
    "-I" + str(REPO / "src"),
]


def build(src):
    obj = out / (
        str(src.relative_to(REPO) if src.is_relative_to(REPO) else src.name)
        .replace("/", "_")
        .replace("\\", "_")
        + ".o"
    )
    if not obj.exists() or src.stat().st_mtime > obj.stat().st_mtime:
        result = subprocess.run(
            [cc, *flags, "-c", str(src), "-o", str(obj)], capture_output=True, text=True
        )
        if result.returncode:
            raise RuntimeError(str(src) + "\n" + result.stderr)
    return str(obj)


with ThreadPoolExecutor(max_workers=4) as pool:
    objects = list(pool.map(build, sources))
exe = out / "decode.exe"
subprocess.run([cc, *objects, "-Wl,--gc-sections", "-lm", "-o", str(exe)], check=True)
frames = []
encoder = Encoder(30, 16, mode="mpeg4", quantizer=3)
for i in range(180):
    frames.append(encoder.encode(test_image(i)))
import struct

with (out / "frames.bin").open("wb") as f:
    for data, *unused in frames:
        f.write(struct.pack("<I", len(data)) + data)
subprocess.run([str(exe), str(out / "frames.bin"), str(out / "pixels.rgb565")], check=True)
import io

# FFmpeg's MPEG4 parser sets per-picture metadata needed by its decoder.
# Passing raw packets straight to that decoder bypasses this and corrupts P pictures.
with av.open(io.BytesIO(b"".join(f[0] for f in frames)), format="m4v") as container:
    container.streams.video[0].codec_context.thread_count = 1
    reference = [f.to_ndarray(format="rgb24").astype(np.int32) for f in container.decode(video=0)]
assert len(reference) == 180
pixels = np.fromfile(out / "pixels.rgb565", dtype="<u2").reshape(180, 240, 320).astype(np.int32)
rgb = np.stack(
    (
        ((pixels >> 11) & 31) * 255 // 31,
        ((pixels >> 5) & 63) * 255 // 63,
        (pixels & 31) * 255 // 31,
    ),
    axis=-1,
)
errors = [float(np.abs(rgb[i] - image).mean()) for i, image in enumerate(reference)]
assert max(errors) < 8, max(errors)
result = dict(
    frames=180,
    quantizer=3,
    keyframes=sum(f[1] for f in frames),
    payload_kbps=sum(len(f[0]) for f in frames) * 8 / 6 / 1000,
    cross_decoder_mean_abs_rgb_error_max=max(errors),
    physical_usb_tested=False,
)
(out / "results.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))

# Change Q on one live codec, including recovery below the initial quality.
encoder = Encoder(30, 16, mode="mpeg4", quantizer=5, preset="medium")
identity = id(encoder.codec)
schedule = [5, 1, 8, 3, 1, 5]
frames = []
for n in range(180):
    if n % 30 == 0:
        encoder.set_quantizer(schedule[n // 30])
    frames.append(encoder.encode(test_image(n)))
    assert id(encoder.codec) == identity
assert [n for n, frame in enumerate(frames) if frame[1]] == [0, 150], "Q changes restarted the GOP"
with (out / "live-q.bin").open("wb") as file:
    for data, *unused in frames:
        file.write(struct.pack("<I", len(data)) + data)
subprocess.run([str(exe), str(out / "live-q.bin"), str(out / "live-q.rgb565")], check=True)
with av.open(io.BytesIO(b"".join(frame[0] for frame in frames)), format="m4v") as stream:
    reference = [
        frame.to_ndarray(format="rgb24").astype(np.int32) for frame in stream.decode(video=0)
    ]
pixels = np.fromfile(out / "live-q.rgb565", dtype="<u2").reshape(180, 240, 320).astype(np.int32)
rgb = np.stack(
    (
        ((pixels >> 11) & 31) * 255 // 31,
        ((pixels >> 5) & 63) * 255 // 63,
        (pixels & 31) * 255 // 31,
    ),
    axis=-1,
)
assert len(reference) == 180
error = max(float(np.abs(rgb[n] - image).mean()) for n, image in enumerate(reference))
assert error < 8, error
assert not encoder.codec.options, "MPEG-4 quality options were silently ignored"
print(
    "PASS live MPEG4 Q5/1/8/3/1/5 and medium RD/trellis through production C decoder; error", error
)
