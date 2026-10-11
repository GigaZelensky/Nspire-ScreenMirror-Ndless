import argparse
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pc"))
from mirror import stream


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--crf", type=int, default=20)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--test-pattern", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frame-metrics", type=Path)
    parser.add_argument("--keyframe-seconds", type=int, default=30)
    display = parser.add_mutually_exclusive_group()
    display.add_argument("--region", type=int, nargs=4)
    display.add_argument(
        "--owned-display", action="store_true", help="Follow the app's currently attached display"
    )
    parser.add_argument("--capture", choices=["dxgi", "gdi", "mss"], default="dxgi")
    parser.add_argument("--mode", choices=["h264", "mpeg4", "lossless", "raw"], default="h264")
    parser.add_argument("--mpeg4-q", type=int, default=3)
    parser.add_argument("--input", action="store_true")
    parser.add_argument(
        "--video",
        type=Path,
        help="Repeat 90 frames at five minutes from a local video for a controlled workload",
    )
    parser.add_argument("--clock", type=int, default=0)
    parser.add_argument("--preset", default="ultrafast")
    parser.add_argument("--pipeline", action="store_true")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--persistent", action="store_true")
    parser.add_argument("--auto-quality", action="store_true")
    parser.add_argument(
        "--replay",
        type=Path,
        help="Replay a recorded gameplay sample at its source timing, skipping stale captures",
    )
    parser.add_argument(
        "--recovery-test",
        action="store_true",
        help="Replay motion for 20 seconds, then a still scene for 10 seconds",
    )
    parser.add_argument(
        "--active-seconds",
        type=float,
        default=0,
        help="Measure this long after USB connects; --seconds remains the wall-clock watchdog",
    )
    args = parser.parse_args()
    stop = threading.Event()
    timer = threading.Timer(args.seconds, stop.set)
    timer.daemon = True
    timer.start()
    args.backend = "windows"
    args.library = None
    args.packet_size = None
    args.monitor = 1
    args.duration = args.active_seconds
    if args.owned_display:
        from virtual_display import WindowsDisplays, owned_device, read_owner

        class AttachedDisplay:
            def __init__(self):
                self.api = WindowsDisplays()
                owner = read_owner()
                devices = [
                    device
                    for device in self.api.devices()
                    if device["flags"] & 1 and owned_device(device, owner)
                ]
                if len(devices) != 1:
                    raise OSError(
                        "Keep the app's virtual display attached while recording timings."
                    )
                self.name = devices[0]["name"]
                self.region = {}

            def refresh_region(self):
                mode = self.api.mode(self.name)
                if mode is None or not mode.width or not mode.height:
                    raise OSError("The calculator display was disconnected during the recording.")
                self.region.update(
                    left=mode.u.display.position.x,
                    top=mode.u.display.position.y,
                    width=mode.width,
                    height=mode.height,
                )
                return self.region

        args.follow_display = AttachedDisplay()
        region = args.follow_display.refresh_region()
        args.region = (region["left"], region["top"], region["width"], region["height"])
        args.capture = "gdi"
    if args.replay:
        import av
        import time
        import mirror

        images = []
        with av.open(str(args.replay)) as container:
            for frame in container.decode(video=0):
                images.append(frame.to_image())
        assert images

        class Replay:
            name = "Recorded gameplay / fresh capture at source time"
            region = dict(left=0, top=0, width=2560, height=1440)

            def __init__(self):
                self.started = None

            def grab(self):
                if self.started is None:
                    self.started = time.monotonic()
                elapsed = time.monotonic() - self.started
                if args.recovery_test and elapsed % 30 >= 20:
                    return images[min(600, len(images) - 1)]
                return images[int(elapsed * 30) % len(images)]

            def close(self):
                pass

        mirror.create_capture = lambda unused: Replay()
    if args.video:
        import av
        import mirror
        from PIL import Image

        frames = []
        with av.open(str(args.video)) as container:
            video = container.streams.video[0]
            container.seek(int(300 / float(video.time_base)), stream=video)
            for frame in container.decode(video=0):
                if float(frame.time or 0) < 300:
                    continue
                image = frame.to_image()
                image.thumbnail((320, 240), Image.Resampling.LANCZOS)
                fitted = Image.new("RGB", (320, 240))
                fitted.paste(image, ((320 - image.width) // 2, (240 - image.height) // 2))
                frames.append(fitted)
                if len(frames) == 90:
                    break
        assert len(frames) == 90

        class RepeatVideo:
            name = "Controlled repeating video / 90 frames"
            region = dict(left=0, top=0, width=2560, height=1440)

            def __init__(self):
                self.index = 0

            def grab(self):
                image = frames[self.index % len(frames)]
                self.index += 1
                return image

            def close(self):
                pass

        mirror.create_capture = lambda unused: RepeatVideo()

    def metrics(data):
        with args.output.open("a") as f:
            f.write(json.dumps(data) + "\n")
        print(json.dumps(data), flush=True)

    stream(args, stop, lambda text: print(text, flush=True), stats_report=metrics)


if __name__ == "__main__":
    main()
