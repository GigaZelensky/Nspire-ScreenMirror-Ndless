"""Compare codecs on identical captured/known motion frames; save metrics, not desktop images."""

import argparse
import io
import json
import sys
import time
from pathlib import Path
import numpy as np
import av
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pc"))
from encoder import Encoder, test_image
from capture import create_capture


def fit(image):
    image = image.convert("RGB")
    image.thumbnail((320, 240), Image.Resampling.LANCZOS)
    fitted = Image.new("RGB", (320, 240))
    fitted.paste(image, ((320 - image.width) // 2, (240 - image.height) // 2))
    return fitted


def luma(image):
    frame = av.VideoFrame.from_image(image).reformat(format="yuv420p")
    plane = frame.planes[0]
    return (
        np.frombuffer(plane, dtype=np.uint8)
        .reshape(240, plane.line_size)[:, :320]
        .astype("float64")
    )


def ssim(a, b):
    # Non-overlapping 8x8 luminance windows; exact same windows for every codec.
    a = a.reshape(30, 8, 40, 8).transpose(0, 2, 1, 3).reshape(-1, 64)
    b = b.reshape(30, 8, 40, 8).transpose(0, 2, 1, 3).reshape(-1, 64)
    x = a.mean(1)
    y = b.mean(1)
    va = a.var(1)
    vb = b.var(1)
    cov = ((a - x[:, None]) * (b - y[:, None])).mean(1)
    return float(
        (
            ((2 * x * y + 6.5025) * (2 * cov + 58.5225))
            / ((x * x + y * y + 6.5025) * (va + vb + 58.5225))
        ).mean()
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "build/quality.json")
    args = parser.parse_args()
    samples = {"pattern": [fit(test_image(i)) for i in range(90)]}
    camera = create_capture(argparse.Namespace(region=None, monitor=1, capture="dxgi"))
    try:
        desktop = []
        for i in range(45):
            desktop.append(fit(camera.grab()))
            time.sleep(1 / 30)
        samples["desktop"] = desktop
    finally:
        camera.close()
    if args.video:
        with av.open(str(args.video)) as container:
            video = container.streams.video[0]
            container.seek(int(300 / float(video.time_base)), stream=video)
            moving = []
            for frame in container.decode(video=0):
                if float(frame.time or 0) < 300:
                    continue
                moving.append(fit(frame.to_image()))
                if len(moving) == 90:
                    break
            assert len(moving) == 90
            samples["animation"] = moving
    results = {}
    for name, images in samples.items():
        reference = [luma(image) for image in images]
        records = []
        for mode, quality in [("h264", 16), ("h264", 14), ("mpeg4", 3), ("mpeg4", 2), ("mpeg4", 1)]:
            encoder = Encoder(30, quality if mode == "h264" else 16, mode=mode, quantizer=quality)
            begin = time.perf_counter()
            packets = [encoder.encode(image)[0] for image in images]
            encoded = time.perf_counter() - begin
            with av.open(
                io.BytesIO(b"".join(packets)), format="h264" if mode == "h264" else "m4v"
            ) as container:
                container.streams.video[0].codec_context.thread_count = 1
                decoded = [luma(frame.to_image()) for frame in container.decode(video=0)]
            assert len(decoded) == len(images)
            mse = float(np.mean([np.square(a - b).mean() for a, b in zip(reference, decoded)]))
            records.append(
                dict(
                    mode=mode,
                    quality=quality,
                    luma_psnr_db=10 * np.log10(255 * 255 / mse) if mse else 100.0,
                    luma_ssim_8x8=float(np.mean([ssim(a, b) for a, b in zip(reference, decoded)])),
                    payload_kbps=sum(map(len, packets)) * 8 * 30 / len(images) / 1000,
                    encode_ms=encoded * 1000 / len(images),
                )
            )
        results[name] = records
    results["method"] = (
        "Identical 320x240 images per codec; decoded through FFmpeg parsers. Luma PSNR and local 8x8 SSIM after RGB conversion. Not a calculator speed or subjective-quality score. Desktop pixels remain in memory only."
    )
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
