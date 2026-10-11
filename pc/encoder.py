from fractions import Fraction
import av
from PIL import Image, ImageDraw


class Encoder:
    def __init__(
        self,
        fps=20,
        crf=20,
        key_seconds=5,
        mode="h264",
        quantizer=3,
        preset="ultrafast",
        bitrate=None,
    ):
        self.mode = mode
        self.codec = av.CodecContext.create("mpeg4" if mode == "mpeg4" else "libx264", "w")
        self.codec.width = 320
        self.codec.height = 240
        self.codec.pix_fmt = "yuv420p"
        self.codec.time_base = Fraction(1, fps)
        self.codec.framerate = Fraction(fps, 1)
        # USB is reliable and reconnect creates a fresh encoder/IDR. Longer
        # GOPs avoid a large once-per-second transfer spike; no seek is needed.
        key_interval = fps * key_seconds
        self.codec.options = {
            "preset": "ultrafast" if preset == "integer" else preset,
            "tune": "zerolatency",
            "profile": "baseline",
            "crf": str(crf),
            "x264-params": f"keyint={key_interval}:min-keyint={key_interval}:scenecut=0:bframes=0:ref=1:cabac=0:8x8dct=0:no-deblock=1:repeat-headers=1:aud=1:threads=1:colorprim=bt470bg:transfer=bt470bg:colormatrix=smpte170m",
        }
        if preset == "integer":
            self.codec.options["x264-params"] += (
                ":me=umh:merange=32:subme=0:partitions=none:aq-mode=0:trellis=0"
            )
        if bitrate is not None:
            if mode != "h264":
                raise ValueError("Adaptive bitrate requires H.264")
            # x264 only reconfigures bitrate in its CBR/VBV path. Bootstrap
            # that path at the ceiling, then set the requested rate before the
            # first frame. No filler bytes: static pictures use less traffic.
            self.codec.options.pop("crf")
            self.codec.bit_rate = 2000000
            self.codec.options.update(maxrate="2000k", bufsize="80k")
        if mode == "mpeg4":
            if type(quantizer) is not int or not 1 <= quantizer <= 8:
                raise ValueError("MPEG-4 quantizer must be 1–8")
            self.codec.options = {"strict": "unofficial"}
            if preset in ("fast", "medium"):
                self.codec.options["mbd"] = "rd"
            if preset == "medium":
                self.codec.options["trellis"] = "2"
            self.codec.flags |= 2 | 0x80000
            # FFmpeg precomputes its matrices from the initial qmin. Include Q1
            # even when opening at a lower quality so live recovery is valid.
            self.codec.global_quality = quantizer
            self.codec.qmin = 1
            self.codec.qmax = 8
            self.codec.gop_size = key_interval
            self.codec.max_b_frames = 0
            self.codec.thread_count = 1
        self.codec.open()
        if mode == "mpeg4" and self.codec.options:
            raise RuntimeError("MPEG-4 encoder rejected options: " + ", ".join(self.codec.options))
        self.index = 0
        if mode == "mpeg4":
            self.set_quantizer(quantizer)
        if bitrate is not None:
            self.set_bitrate(bitrate)

    def set_bitrate(self, bitrate):
        if self.mode != "h264":
            raise ValueError("Live bitrate changes require H.264")
        self.codec.bit_rate = int(bitrate)

    def set_quantizer(self, quantizer):
        if self.mode != "mpeg4" or type(quantizer) is not int or not 1 <= quantizer <= 8:
            raise ValueError("Live MPEG-4 quantizer must be 1–8")
        # PyAV's global_quality property already converts Q to FFmpeg's lambda units.
        self.codec.global_quality = quantizer
        self.codec.qmin = self.codec.qmax = quantizer
        self.quantizer = quantizer

    def set_quality(self, value):
        if self.mode == "mpeg4":
            self.set_quantizer(value)
        else:
            self.set_bitrate(value)

    def encode(self, image):
        if image.mode != "RGB":
            image = image.convert("RGB")
        if image.size == (320, 240):
            fitted = image
        else:
            scale = min(320 / image.width, 240 / image.height)
            size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            if size != image.size:
                image = image.resize(size, Image.Resampling.LANCZOS)
            fitted = Image.new("RGB", (320, 240))
            fitted.paste(image, ((320 - image.width) // 2, (240 - image.height) // 2))
        frame = av.VideoFrame(320, 240, "rgb24")
        # Avoid a NumPy conversion/copy and its runtime dependency.
        if frame.planes[0].line_size != 960:
            raise RuntimeError("Unexpected RGB capture stride")
        frame.planes[0].update(fitted.tobytes())
        frame.pts = self.index
        frame.time_base = self.codec.time_base
        self.index += 1
        packets = self.codec.encode(frame)
        if len(packets) != 1:
            raise RuntimeError("Live encoder did not produce exactly one access unit")
        result = (bytes(packets[0]), packets[0].is_keyframe)
        if self.mode == "mpeg4":
            from wire import MPEG4

            return *result, MPEG4
        return result


def test_image(index):
    image = Image.new("RGB", (320, 240), (18, 20, 23))
    draw = ImageDraw.Draw(image)
    draw.text((14, 12), "ND SCREEN MIRROR / colour + motion", fill="white")
    for n, color in enumerate(["red", "green", "blue", "cyan", "magenta", "yellow"]):
        draw.rectangle((n * 53, 45, n * 53 + 52, 105), fill=color)
    x = (index * 5) % 280
    draw.rectangle((x, 132, x + 39, 171), fill=(246, 158, 47))
    draw.text((14, 203), f"PC-generated test pattern: {index}", fill="white")
    return image
