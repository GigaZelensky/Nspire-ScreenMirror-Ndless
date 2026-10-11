import struct
import numpy as np
import lz4.block
from PIL import Image
from wire import RAW, DELTA, LZ4


class PixelEncoder:
    def __init__(self, raw=False):
        self.previous = None
        self.raw = raw

    def encode(self, image):
        fitted = Image.new("RGB", (320, 240))
        image = image.convert("RGB")
        image.thumbnail((320, 240), Image.Resampling.LANCZOS)
        fitted.paste(image, ((320 - image.width) // 2, (240 - image.height) // 2))
        rgb = np.asarray(fitted, dtype=np.uint16)
        pixels = ((rgb[:, :, 0] >> 3) << 11) | ((rgb[:, :, 1] >> 2) << 5) | (rgb[:, :, 2] >> 3)
        pixels = pixels.astype("<u2")
        first = self.previous is None
        if self.raw:
            return pixels.tobytes(), True, RAW
        if first:
            payload = pixels.tobytes()
            flags = RAW | LZ4
        else:
            dirty = (
                (pixels != self.previous)
                .reshape(15, 16, 20, 16)
                .transpose(0, 2, 1, 3)
                .any(axis=(2, 3))
            )
            # Merge horizontal tile runs, then combine identical runs vertically.
            rectangles = []
            active = {}
            for y in range(15):
                runs = []
                x = 0
                while x < 20:
                    if not dirty[y, x]:
                        x += 1
                        continue
                    start = x
                    while x < 20 and dirty[y, x]:
                        x += 1
                    runs.append((start, x - start))
                next_active = {}
                for x, w in runs:
                    if (x, w) in active:
                        index = active[(x, w)]
                        rectangles[index][3] += 16
                    else:
                        index = len(rectangles)
                        rectangles.append([x * 16, y * 16, w * 16, 16])
                    next_active[(x, w)] = index
                active = next_active
            chunks = [struct.pack("<HH", len(rectangles), 0)]
            for x, y, w, h in rectangles:
                chunks.append(struct.pack("<4H", x, y, w, h))
                chunks.append(pixels[y : y + h, x : x + w].tobytes())
            payload = b"".join(chunks)
            flags = DELTA | LZ4
        self.previous = pixels
        return lz4.block.compress(payload, mode="fast", store_size=True), first, flags
