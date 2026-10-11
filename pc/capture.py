"""Capture directly at receiver resolution on Windows; retain MSS for Linux."""

import ctypes as c
import os
import re
from PIL import Image


class TimingLease:
    def __init__(self):
        self.end = None
        if os.name == "nt":
            lib = c.WinDLL("winmm")
            begin = lib.timeBeginPeriod
            begin.argtypes = [c.c_uint]
            begin.restype = c.c_uint
            end = lib.timeEndPeriod
            end.argtypes = begin.argtypes
            end.restype = c.c_uint
            if begin(1) != 0:
                raise OSError("Windows could not provide the requested stream timer resolution")
            self.end = end

    def close(self):
        if self.end:
            self.end(1)
            self.end = None


class MssCapture:
    name = "MSS desktop capture"

    def __init__(self, reader, region):
        self.reader = reader
        self.region = dict(region)

    def grab(self):
        shot = self.reader.grab(self.region)
        # Convert BGRX in Pillow's C loop, avoiding MSS's full RGB slice copies.
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def close(self):
        self.reader.close()


class WindowsScaledCapture:
    name = "Windows capture at 320 x 240"

    def __init__(self, region, cursor=True, region_source=None):
        from ctypes import wintypes as w

        class Header(c.Structure):
            _fields_ = [
                ("size", w.DWORD),
                ("width", w.LONG),
                ("height", w.LONG),
                ("planes", w.WORD),
                ("bits", w.WORD),
                ("compression", w.DWORD),
                ("bytes", w.DWORD),
                ("xppm", w.LONG),
                ("yppm", w.LONG),
                ("used", w.DWORD),
                ("important", w.DWORD),
            ]

        class Info(c.Structure):
            _fields_ = [("header", Header), ("colors", w.DWORD * 3)]

        class Cursor(c.Structure):
            _fields_ = [
                ("size", w.DWORD),
                ("flags", w.DWORD),
                ("handle", w.HANDLE),
                ("point", w.POINT),
            ]

        class Icon(c.Structure):
            _fields_ = [
                ("is_icon", w.BOOL),
                ("x", w.DWORD),
                ("y", w.DWORD),
                ("mask", w.HANDLE),
                ("color", w.HANDLE),
            ]

        class Bitmap(c.Structure):
            _fields_ = [
                ("type", w.LONG),
                ("width", w.LONG),
                ("height", w.LONG),
                ("stride", w.LONG),
                ("planes", w.WORD),
                ("bits", w.WORD),
                ("data", c.c_void_p),
            ]

        self.Cursor, self.Icon, self.Bitmap = Cursor, Icon, Bitmap
        self.user = c.WinDLL("user32", use_last_error=True)
        self.gdi = c.WinDLL("gdi32", use_last_error=True)

        def bind(lib, name, args, result):
            f = getattr(lib, name)
            f.argtypes = args
            f.restype = result
            return f

        self.get_dc = bind(self.user, "GetDC", [w.HWND], w.HDC)
        self.release_dc = bind(self.user, "ReleaseDC", [w.HWND, w.HDC], c.c_int)
        self.create_dc = bind(self.gdi, "CreateCompatibleDC", [w.HDC], w.HDC)
        self.delete_dc = bind(self.gdi, "DeleteDC", [w.HDC], w.BOOL)
        self.create_bitmap = bind(
            self.gdi,
            "CreateDIBSection",
            [w.HDC, c.POINTER(Info), w.UINT, c.POINTER(c.c_void_p), w.HANDLE, w.DWORD],
            w.HBITMAP,
        )
        self.select = bind(self.gdi, "SelectObject", [w.HDC, w.HGDIOBJ], w.HGDIOBJ)
        self.delete = bind(self.gdi, "DeleteObject", [w.HGDIOBJ], w.BOOL)
        self.stretch = bind(
            self.gdi,
            "StretchBlt",
            [w.HDC, *([c.c_int] * 4), w.HDC, *([c.c_int] * 4), w.DWORD],
            w.BOOL,
        )
        self.mode = bind(self.gdi, "SetStretchBltMode", [w.HDC, c.c_int], c.c_int)
        self.origin = bind(
            self.gdi, "SetBrushOrgEx", [w.HDC, c.c_int, c.c_int, c.POINTER(w.POINT)], w.BOOL
        )
        self.flush = bind(self.gdi, "GdiFlush", [], w.BOOL)
        self.get_cursor = bind(self.user, "GetCursorInfo", [c.POINTER(Cursor)], w.BOOL)
        self.icon_info = bind(self.user, "GetIconInfo", [w.HICON, c.POINTER(Icon)], w.BOOL)
        self.object_info = bind(self.gdi, "GetObjectW", [w.HANDLE, c.c_int, c.c_void_p], c.c_int)
        self.draw_icon = bind(
            self.user,
            "DrawIconEx",
            [w.HDC, c.c_int, c.c_int, w.HICON, c.c_int, c.c_int, w.UINT, w.HBRUSH, w.UINT],
            w.BOOL,
        )
        self.region = region
        self.region_source = region_source
        self.cursor = cursor
        scale = min(320 / region["width"], 240 / region["height"])
        self.width = max(1, round(region["width"] * scale))
        self.height = max(1, round(region["height"] * scale))
        self.x = (320 - self.width) // 2
        self.y = (240 - self.height) // 2
        self.source = self.dest = self.bitmap = self.old = None
        self.pixels = c.c_void_p()
        try:
            self.source = self.get_dc(None)
            self.dest = self.create_dc(self.source)
            if not self.source or not self.dest:
                raise c.WinError(c.get_last_error())
            info = Info()
            info.header = Header(40, 320, -240, 1, 32, 0, 320 * 240 * 4, 0, 0, 0, 0)
            self.bitmap = self.create_bitmap(
                self.source, c.byref(info), 0, c.byref(self.pixels), None, 0
            )
            if not self.bitmap or not self.pixels:
                raise c.WinError(c.get_last_error())
            self.old = self.select(self.dest, self.bitmap)
            if not self.old or self.old == c.c_void_p(-1).value:
                raise c.WinError(c.get_last_error())
            if not self.mode(self.dest, 4) or not self.origin(self.dest, 0, 0, None):
                raise c.WinError(c.get_last_error())
        except BaseException:
            self.close()
            raise

    def update_region(self, region):
        if all(self.region[key] == region[key] for key in ("left", "top", "width", "height")):
            return
        source = self.get_dc(None)
        if not source:
            raise c.WinError(c.get_last_error())
        if self.source:
            self.release_dc(None, self.source)
        self.source = source
        # Remote input holds this same dict; update it instead of replacing it.
        self.region.update(region)
        scale = min(320 / region["width"], 240 / region["height"])
        self.width = max(1, round(region["width"] * scale))
        self.height = max(1, round(region["height"] * scale))
        self.x = (320 - self.width) // 2
        self.y = (240 - self.height) // 2

    def grab(self):
        if self.region_source:
            self.update_region(self.region_source.refresh_region())
        c.memset(self.pixels, 0, 320 * 240 * 4)
        r = self.region
        if not self.stretch(
            self.dest,
            self.x,
            self.y,
            self.width,
            self.height,
            self.source,
            r["left"],
            r["top"],
            r["width"],
            r["height"],
            0x40CC0020,
        ):
            raise c.WinError(c.get_last_error())
        if self.cursor:
            self._draw_cursor()
        if not self.flush():
            raise c.WinError(c.get_last_error())
        return Image.frombytes(
            "RGB", (320, 240), c.string_at(self.pixels, 320 * 240 * 4), "raw", "BGRX"
        )

    def _draw_cursor(self):
        cursor = self.Cursor()
        cursor.size = c.sizeof(cursor)
        if not self.get_cursor(c.byref(cursor)) or not cursor.flags & 1:
            return
        r = self.region
        px = cursor.point.x - r["left"]
        py = cursor.point.y - r["top"]
        if not 0 <= px < r["width"] or not 0 <= py < r["height"]:
            return
        icon = self.Icon()
        if not self.icon_info(cursor.handle, c.byref(icon)):
            return
        try:
            bitmap = self.Bitmap()
            handle = icon.color or icon.mask
            if not handle or not self.object_info(handle, c.sizeof(bitmap), c.byref(bitmap)):
                return
            sx = self.width / r["width"]
            sy = self.height / r["height"]
            source_height = bitmap.height // (1 if icon.color else 2)
            # Keep the real system cursor recognizable on a 320-pixel screen.
            scale = max(sx, sy, 14 / max(bitmap.width, source_height))
            width = max(1, round(bitmap.width * scale))
            height = max(1, round(source_height * scale))
            self.draw_icon(
                self.dest,
                self.x + round(px * sx - icon.x * scale),
                self.y + round(py * sy - icon.y * scale),
                cursor.handle,
                width,
                height,
                0,
                None,
                3,
            )
        finally:
            if icon.color:
                self.delete(icon.color)
            if icon.mask:
                self.delete(icon.mask)

    def close(self):
        if self.old and self.dest:
            self.select(self.dest, self.old)
            self.old = None
        if self.bitmap:
            self.delete(self.bitmap)
            self.bitmap = None
        if self.dest:
            self.delete_dc(self.dest)
            self.dest = None
        if self.source:
            self.release_dc(None, self.source)
            self.source = None


class CursorPainter(WindowsScaledCapture):
    """Draw the actual Windows cursor after scaling, including cached frames."""

    def paint(self, image):
        c.memset(self.pixels, 0, 320 * 240 * 4)
        width, height = image.size
        x = (320 - width) // 2
        y = (240 - height) // 2
        data = image.tobytes("raw", "BGRX")
        if width == 320:
            c.memmove(self.pixels.value + y * 1280, data, len(data))
        else:
            for row in range(height):
                chunk = data[row * width * 4 : (row + 1) * width * 4]
                c.memmove(self.pixels.value + (y + row) * 1280 + x * 4, chunk, len(chunk))
        self._draw_cursor()
        if not self.flush():
            raise c.WinError(c.get_last_error())
        return Image.frombytes(
            "RGB", (320, 240), c.string_at(self.pixels, 320 * 240 * 4), "raw", "BGRX"
        )


class DxgiCapture:
    name = "DXGI desktop capture"

    def __init__(self, region):
        import dxcam

        self.camera = None
        self.last = None
        self.cursor_painter = None
        # Match actual desktop coordinates, not an assumed monitor/GPU order.
        for device, output in re.findall(r"Device\[(\d+)\] Output\[(\d+)\]", dxcam.output_info()):
            candidate = dxcam.create(
                device_idx=int(device),
                output_idx=int(output),
                output_color="BGRA",
                processor_backend="numpy",
                max_buffer_len=2,
            )
            bounds = candidate._output.desc.DesktopCoordinates
            if (
                bounds.left <= region["left"]
                and bounds.top <= region["top"]
                and bounds.right >= region["left"] + region["width"]
                and bounds.bottom >= region["top"] + region["height"]
            ):
                self.camera = candidate
                self.region = (
                    region["left"] - bounds.left,
                    region["top"] - bounds.top,
                    region["left"] - bounds.left + region["width"],
                    region["top"] - bounds.top + region["height"],
                )
                break
            candidate.release()
        if not self.camera:
            raise ValueError(
                "DXGI captures one monitor. Use GDI/MSS for a region spanning monitors."
            )
        self.desktop_region = region
        try:
            self.cursor_painter = CursorPainter(region)
        except BaseException:
            self.close()
            raise

    def grab(self):
        frame = self.camera.grab(region=self.region, new_frame_only=True, copy=False)
        if frame is None:
            if self.last is None:
                raise OSError("DXGI has not provided its first desktop frame")
            return self.cursor_painter.paint(self.last) if self.cursor_painter else self.last
        if not frame.flags.c_contiguous:
            frame = frame.copy()
        # Map BGRA bytes as RGBX without touching millions of channel values;
        # resize first, then reorder only the 320x240 output. X has no alpha.
        image = Image.frombuffer(
            "RGBX", (frame.shape[1], frame.shape[0]), frame, "raw", "RGBX", 0, 1
        )
        scale = min(320 / image.width, 240 / image.height)
        width = max(1, round(image.width * scale))
        height = max(1, round(image.height * scale))
        scaled = image.resize((width, height), Image.Resampling.LANCZOS, reducing_gap=2)
        small = Image.frombytes("RGB", (width, height), scaled.tobytes(), "raw", "BGRX")
        self.last = small
        return self.cursor_painter.paint(small) if self.cursor_painter else small

    def close(self):
        if self.camera:
            self.camera.release()
            self.camera = None
        if self.cursor_painter:
            self.cursor_painter.close()
            self.cursor_painter = None


def create_capture(args):
    backend = getattr(args, "capture", None)
    if os.name != "nt" and (
        backend == "wayland"
        or backend in (None, "auto")
        and (os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY"))
    ):
        from wayland_capture import WaylandCapture

        return WaylandCapture(args)
    if backend == "wayland":
        raise ValueError("Wayland capture requires Linux")
    if os.name != "nt" and backend in ("dxgi", "gdi", "native"):
        raise ValueError("Select Auto, Wayland or MSS capture on Linux")
    import mss

    reader = getattr(mss, "MSS", mss.mss)(**({"with_cursor": True} if os.name != "nt" else {}))
    try:
        if args.region:
            x, y, width, height = args.region
            region = dict(left=x, top=y, width=width, height=height)
        else:
            if not 0 <= args.monitor < len(reader.monitors):
                raise ValueError("Monitor is unavailable")
            region = reader.monitors[args.monitor]
        if backend in (None, "auto"):
            backend = "dxgi" if os.name == "nt" else "mss"
        if os.name == "nt" and backend == "dxgi":
            reader.close()
            return DxgiCapture(region)
        if os.name == "nt" and backend in ("native", "gdi"):
            reader.close()
            return WindowsScaledCapture(region, region_source=getattr(args, "follow_display", None))
        return MssCapture(reader, region)
    except BaseException:
        reader.close()
        raise
