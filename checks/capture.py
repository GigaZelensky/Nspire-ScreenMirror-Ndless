from pathlib import Path
import sys
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from capture import DxgiCapture, WindowsScaledCapture


class Camera:
    def __init__(self, frame):
        self.frame = frame

    def grab(self, **unused):
        return self.frame

    def release(self):
        pass


rng = np.random.default_rng(11)
for shape in [(96, 160, 4), (480, 640, 4), (144, 256, 4)]:
    frame = rng.integers(0, 256, shape, dtype=np.uint8)
    camera = DxgiCapture.__new__(DxgiCapture)
    camera.camera = Camera(frame)
    camera.region = None
    camera.last = None
    camera.cursor_painter = None
    actual = camera.grab()
    full = Image.frombytes("RGB", (shape[1], shape[0]), frame, "raw", "BGRX")
    expected = full.resize(actual.size, Image.Resampling.LANCZOS, reducing_gap=2)
    assert actual.tobytes() == expected.tobytes(), shape
    camera.camera.frame = None
    assert camera.grab() is actual
    camera.close()
print(
    "PASS zero-copy BGRA scaling preserves RGB order, ignores alpha and reuses the last frame safely"
)
capture = WindowsScaledCapture.__new__(WindowsScaledCapture)
capture.region = dict(left=2560, top=0, width=320, height=240)
pointer_region = capture.region
capture.source = 7
released = []
capture.get_dc = lambda unused: 8
capture.release_dc = lambda unused, handle: released.append(handle)
capture.update_region(dict(left=-640, top=1200, width=640, height=480))
assert capture.region is pointer_region and pointer_region["top"] == 1200
assert (capture.width, capture.height, capture.x, capture.y) == (320, 240, 0, 0)
assert released == [7]
capture.update_region(dict(left=-640, top=1200, width=720, height=405))
assert (capture.width, capture.height, capture.x, capture.y) == (320, 180, 0, 30)
print(
    "PASS moving display refreshes capture DC and shared input region; resizing retains letterboxing"
)
