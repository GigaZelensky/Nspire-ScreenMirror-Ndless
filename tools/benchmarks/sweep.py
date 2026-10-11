"""Quality and physical FPS sweep using one USB helper and the same source frames."""

import argparse
import io
import json
import sys
import time
import threading
from pathlib import Path
import av
import numpy as np
from PIL import ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pc"))
from encoder import Encoder
from transport import Bridge
from capture import create_capture
import mirror
from quality import fit, luma, ssim


def sources(video):
    result = {}
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        container.seek(int(300 / float(stream.time_base)), stream=stream)
        images = []
        for frame in container.decode(video=0):
            if float(frame.time or 0) < 300:
                continue
            images.append(fit(frame.to_image()))
            if len(images) == 90:
                break
        assert len(images) == 90
        result["video"] = images
    camera = create_capture(argparse.Namespace(region=None, monitor=1, capture="dxgi"))
    try:
        base = fit(camera.grab())
    finally:
        camera.close()
    images = []
    for i in range(90):
        image = base.copy()
        scroll = base.crop((20, 55, 290, 205))
        offset = i * 2 % 150
        image.paste(scroll.crop((0, offset, 270, 150)), (20, 55))
        image.paste(scroll.crop((0, 0, 270, offset)), (20, 205 - offset))
        draw = ImageDraw.Draw(image)
        draw.rectangle((15 + i * 3 % 260, 18, 35 + i * 3 % 260, 38), fill=(245, 160, 42))
        images.append(image)
    result["desktop_scroll"] = images
    return result


class Capture:
    region = dict(left=0, top=0, width=2560, height=1440)
    name = "Controlled identical-frame workload"

    def __init__(self, frames):
        self.frames = frames
        self.index = 0

    def grab(self):
        image = self.frames[self.index % len(self.frames)]
        self.index += 1
        return image

    def close(self):
        pass


class Borrowed:
    def __init__(self, bridge, stop):
        self.bridge = bridge
        bridge.stop = stop

    def connection(self):
        return self.bridge.connection()

    def exchange(self, data):
        return self.bridge.exchange(data)

    def batch(self, data):
        return self.bridge.batch(data)

    def close(self):
        pass


def score(images, crf, preset):
    encoder = Encoder(30, crf, preset=preset)
    begin = time.perf_counter()
    data = [encoder.encode(image)[0] for image in images]
    encoding = (time.perf_counter() - begin) * 1000 / len(images)
    with av.open(io.BytesIO(b"".join(data)), format="h264") as container:
        container.streams.video[0].codec_context.thread_count = 1
        decoded = [luma(frame.to_image()) for frame in container.decode(video=0)]
    assert len(decoded) == len(images)
    reference = [luma(image) for image in images]
    mse = np.mean([np.square(a - b).mean() for a, b in zip(reference, decoded)])
    return dict(
        crf=crf,
        preset=preset,
        luma_psnr_db=float(10 * np.log10(255 * 255 / mse)),
        ssim=float(np.mean([ssim(a, b) for a, b in zip(reference, decoded)])),
        payload_at_30_kbps=sum(map(len, data)) * 8 / len(images) * 30 / 1000,
        pc_encode_ms=encoding,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=8)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=["presets", "rates", "integer"], default="presets")
    parser.add_argument("--preset", default="ultrafast")
    args = parser.parse_args()
    images = sources(args.video)
    results = []
    configs = (
        [(14, p, 60) for p in ["ultrafast", "veryfast", "fast", "medium"]]
        if args.phase == "presets"
        else [(q, args.preset, f) for q in [18, 14, 10, 8] for f in [24, 30, 40, 60]]
    )
    if args.phase == "integer":
        configs = [
            (14, "ultrafast", 30),
            (14, "integer", 30),
            (12, "integer", 30),
            (10, "integer", 30),
            (14, "integer", 60),
            (12, "integer", 60),
        ]
    quality = {}
    for name, frames in images.items():
        for q, p, f in configs:
            key = (name, q, p)
            if key not in quality:
                quality[key] = score(frames, q, p)
    bridge = Bridge(threading.Event(), "windows")
    try:
        for name, frames in images.items():
            for q, p, f in configs:
                stop = threading.Event()
                records = []
                messages = []
                options = argparse.Namespace(
                    test_pattern=False,
                    fps=f,
                    crf=q,
                    preset=p,
                    clock=492,
                    mode="h264",
                    input=False,
                    duration=args.seconds,
                    backend="windows",
                    library=None,
                    packet_size=None,
                    region=None,
                    monitor=1,
                )
                mirror.create_capture = lambda unused, frames=frames: Capture(frames)
                watchdog = threading.Timer(60 + args.seconds, stop.set)
                watchdog.daemon = True
                watchdog.start()
                print(f"CASE {name} CRF {q} preset {p} target {f}", flush=True)
                mirror.stream(
                    options,
                    stop,
                    lambda value: messages.append(value),
                    bridge_factory=lambda stop, *unused: Borrowed(bridge, stop),
                    stats_report=records.append,
                )
                watchdog.cancel()
                if not records:
                    raise RuntimeError("No physical measurements: " + repr(messages))
                record = {"workload": name, **quality[(name, q, p)], **records[-1]}
                record["messages"] = [m for m in messages if "Retrying" in m]
                results.append(record)
                args.output.write_text(
                    json.dumps(
                        dict(
                            method="Same 90 source frames per case; 492 MHz; capture work excluded; 512-frame capture-to-ack timing window, not panel latency; quality through FFmpeg; desktop pixels never saved",
                            cases=results,
                        ),
                        indent=2,
                    )
                    + "\n"
                )
                print(json.dumps(record), flush=True)
                if record["messages"]:
                    raise RuntimeError(
                        "Interrupted case; retain partial results and resolve before comparing"
                    )
    finally:
        bridge.stop = threading.Event()
        bridge.close()


if __name__ == "__main__":
    main()
