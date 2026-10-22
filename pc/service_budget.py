"""Small, bounded frame-cost model; no image processing or extra encoding."""

from collections import deque
import math


class ServiceBudget:
    def __init__(self):
        self.samples = deque(maxlen=120)
        self.capacity = None
        self.confidence = 0.0

    def observe(self, now, size, service, capture, encode, bitrate, band, keyframe=False):
        if size <= 0 or keyframe or service <= 0 or service > 0.25:
            return
        local = max(0.0, capture) + max(0.0, encode)
        receiver = max(0.0, service - local)
        self.samples.append((now, size, receiver, local, bitrate, band))
        while self.samples and self.samples[0][0] < now - 6:
            self.samples.popleft()

    def estimate(self, fps, band):
        self.capacity = None
        self.confidence = 0.0
        rows = [s for s in self.samples if s[5] == band]
        if len(rows) < 15:
            return None
        n = len(rows)
        x = sum(s[1] for s in rows) / n
        y = sum(s[2] for s in rows) / n
        xx = sum((s[1] - x) ** 2 for s in rows)
        yy = sum((s[2] - y) ** 2 for s in rows)
        xy = sum((s[1] - x) * (s[2] - y) for s in rows)
        # A nearly constant frame size or noisy/nonpositive slope cannot prove
        # capacity. In that case use measured headroom and conservative probes.
        if xx < n * max(128, x * 0.08) ** 2 or yy <= 0 or xy <= 0:
            return None
        self.confidence = xy * xy / (xx * yy)
        if self.confidence < 0.35:
            return None
        slope = xy / xx
        fixed = max(0.0, y - slope * x)
        local = sorted(s[3] for s in rows)[int((n - 1) * 0.8)]
        available = 0.90 / fps - local - fixed
        utilization = sum(s[1] for s in rows) * fps * 8 / max(1, sum(s[4] for s in rows))
        if available <= 0 or utilization < 0.2:
            return None
        capacity = available / slope * fps * 8 / utilization
        if math.isfinite(capacity) and capacity > 0:
            self.capacity = int(capacity)
        return self.capacity
