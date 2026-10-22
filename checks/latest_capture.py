from pathlib import Path
import threading
import time
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from latest_capture import LatestCapture
import latest_capture

# Different clock epochs expose accidental mixing of coarse Windows
# monotonic timestamps with the sender's precise perf_counter timestamps.
latest_capture.time = SimpleNamespace(
    perf_counter=time.perf_counter, monotonic=lambda: time.perf_counter() - 1000000
)


class Camera:
    name = "Mock capture"
    region = {}
    closed = False
    owner = None
    count = 0

    def __init__(self, unused):
        self.owner = threading.get_ident()

    def grab(self):
        assert threading.get_ident() == self.owner
        self.count += 1
        time.sleep(0.002)
        return self.count

    def close(self):
        assert threading.get_ident() == self.owner
        self.closed = True


cameras = []


def factory(args):
    camera = Camera(args)
    cameras.append(camera)
    return camera


capture = LatestCapture(None, factory, rate=200)
try:
    first = capture.grab()
    time.sleep(0.08)
    latest = capture.grab()
    assert latest > first + 5
    after = time.perf_counter()
    capture.grab(after=after)
    assert capture.last_sample[2] >= after
    assert len(capture.frame) == 3  # one frame, no unbounded queue
finally:
    capture.close()
assert cameras[0].closed and not capture.thread.is_alive()


class Broken(Camera):
    def grab(self):
        raise OSError("capture failed")


capture = None
try:
    try:
        capture = LatestCapture(None, Broken)
        capture.grab()
    except OSError as error:
        assert str(error) == "capture failed"
    else:
        raise AssertionError("Capture error was hidden")
finally:
    if capture:
        capture.close()
print(
    "PASS capture thread ownership, single latest frame, input freshness, failure propagation and cleanup"
)
