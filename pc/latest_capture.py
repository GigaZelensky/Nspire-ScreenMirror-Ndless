"""One replaceable captured frame; no screenshot backlog or encoding ahead."""

import threading
import time
import argparse
import os


class LatestCapture:
    def __init__(self, args, factory, rate=60):
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.ready = threading.Event()
        self.frame = None
        self.error = None
        self.closed = False
        self.last_sample = None
        self.name = "Latest desktop capture"
        self.region = None
        self.desktop_region = None
        self.thread = threading.Thread(
            target=self._run,
            args=(
                argparse.Namespace(
                    **{**(vars(args) if args is not None else {}), "stop_event": self.stop}
                ),
                factory,
                rate,
            ),
            daemon=True,
            name="ND latest capture",
        )
        self.thread.start()
        portal = (
            getattr(args, "capture", None) == "wayland"
            or os.name != "nt"
            and (
                os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY")
            )
        )
        deadline = time.perf_counter() + (185 if portal else 8)
        while not self.ready.wait(0.05):
            cancelled = getattr(args, "stop_event", None)
            if cancelled is not None and cancelled.is_set():
                self.close()
                raise InterruptedError("Capture stopped")
            if time.perf_counter() >= deadline:
                self.close()
                raise TimeoutError("Capture initialization timed out")
        if self.error:
            self.close()
            raise self.error

    def _run(self, args, factory, rate):
        camera = None
        try:
            camera = factory(args)
            self.name = camera.name + " / latest frame"
            self.region = getattr(camera, "region", None)
            self.desktop_region = getattr(camera, "desktop_region", None)
            self.ready.set()
            due = time.perf_counter()
            while not self.stop.is_set():
                started = time.perf_counter()
                image = camera.grab()
                finished = time.perf_counter()
                with self.condition:
                    self.frame = (image, started, finished)
                    self.condition.notify_all()
                due += 1 / rate
                if due < finished:
                    due = finished
                self.wake.wait(max(0, due - time.perf_counter()))
                self.wake.clear()
        except Exception as error:
            with self.condition:
                self.error = error
                self.condition.notify_all()
            self.ready.set()
        finally:
            try:
                if camera:
                    camera.close()
            except Exception as error:
                self.error = self.error or error
            with self.condition:
                self.closed = True
                self.condition.notify_all()

    def grab(self, after=0):
        deadline = time.perf_counter() + 2
        with self.condition:
            if self.frame is None or self.frame[2] < after:
                self.wake.set()
            while self.frame is None or self.frame[2] < after:
                if self.error:
                    raise self.error
                if self.closed:
                    raise IOError("Capture stopped")
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise TimeoutError("No fresh desktop capture")
                self.condition.wait(min(0.05, remaining))
            if self.error:
                raise self.error
            self.last_sample = self.frame
            return self.frame[0]

    def close(self):
        self.stop.set()
        self.wake.set()
        with self.condition:
            self.condition.notify_all()
        self.thread.join(2)
        if self.thread.is_alive():
            raise TimeoutError("Desktop capture did not stop")
