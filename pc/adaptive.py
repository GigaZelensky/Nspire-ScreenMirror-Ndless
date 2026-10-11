"""Use recent receiver progress and unpaced service cost to control live ABR."""

from collections import deque, Counter
import math
from PIL import Image, ImageChops, ImageStat


class AdaptiveQuality:
    def __init__(self, fps, bitrate=700000, minimum=125000, maximum=2000000, protect_idle=False):
        self.fps = fps
        self.minimum = minimum
        self.maximum = maximum
        self.bitrate = max(minimum, min(maximum, bitrate))
        self.samples = deque()
        self.last_change = None
        self.last_reduction = None
        self.good_since = None
        self.bad_since = None
        self.last_check = None
        self.bytes_sent = 0
        self.previous_image = None
        self.motion_streak = 0
        self.still_streak = 0
        self.moving = False
        self.moving_since = None
        self.safe_motion_bitrate = None
        self.predictions = 0
        self.motion_band = 0
        self.pending_band = 0
        self.band_streak = 0
        self.lower_since = None
        self.safe_rates = {}
        self.capture_ms = 0.0
        self.short_fps = 0.0
        self.motion_average = 0.0
        self.motion_score = 0.0
        self.spike_streak = 0
        self.safe_levels = {}
        self.display_fps = 0.0
        self.service_ms = 0.0
        self.reason = "warming up"
        self.changes = 0
        self.protect_idle = protect_idle

    def prepare(self, image, now):
        small = image.resize((32, 24), Image.Resampling.BILINEAR).convert("L")
        difference = (
            ImageStat.Stat(ImageChops.difference(small, self.previous_image)).mean[0]
            if self.previous_image is not None
            else 0
        )
        baseline = self.motion_average
        self.motion_score = difference
        spike = baseline > 2.5 and difference > max(6.0, baseline * 1.6)
        self.spike_streak = self.spike_streak + 1 if spike else 0
        self.motion_average += 0.12 * (difference - self.motion_average)
        incoming = 0 if difference <= 2.5 else 1 if difference < 6 else 2 if difference < 12 else 3
        changed = incoming > 0
        self.previous_image = small
        self.motion_streak = self.motion_streak + 1 if changed else 0
        self.still_streak = 0 if changed else self.still_streak + 1
        if self.still_streak >= 8:
            self.moving = False
            self.moving_since = None
            self.motion_band = 0
        if incoming < self.motion_band:
            if self.lower_since is None:
                self.lower_since = now
            if now - self.lower_since >= 0.25:
                self.motion_band = incoming
        else:
            self.lower_since = None
        self.band_streak = self.band_streak + 1 if incoming == self.pending_band else 1
        self.pending_band = incoming
        increasing_band = incoming > self.motion_band and self.band_streak >= 2
        increasing_speed = self.spike_streak >= 2
        if not incoming or not (increasing_band or increasing_speed):
            return None
        self.spike_streak = 0
        previous_band = self.motion_band
        self.motion_band = incoming
        self.moving = True
        self.moving_since = now
        # Slow and fast motion have separate learned budgets: slow movement
        # cannot overwrite the rate needed for a sudden fast camera movement.
        candidates = (
            [self.safe_rates[incoming]]
            if incoming in self.safe_rates
            else [rate for band, rate in self.safe_rates.items() if band > incoming]
            if incoming >= 2
            else []
        )
        target = (
            min(candidates) if candidates else self.safe_motion_bitrate if incoming >= 2 else None
        )
        reference = self.safe_levels.get(incoming)
        if target is not None and reference is not None and difference > reference * 1.4:
            target = int(target * max(0.3, (reference / difference) ** 0.8))
        elif increasing_speed and target is not None:
            target = min(target, int(self.bitrate * max(0.3, min(0.85, baseline / difference))))
        if not candidates and previous_band and target is not None:
            target = min(target, int(self.bitrate * (previous_band + 1) / (incoming + 1)))
        if target is not None and self.bitrate > target * 1.2:
            self.bitrate = max(self.minimum, target // 1000 * 1000)
            self.last_change = now
            self.last_reduction = now
            self.good_since = self.bad_since = None
            self.changes += 1
            self.predictions += 1
            self.reason = "motion increased: using learned budget"
            return self.bitrate
        return None

    def observe(self, now, displayed, service_seconds, capture_seconds, bytes_sent=0):
        self.bytes_sent += bytes_sent
        self.samples.append(
            (
                now,
                displayed,
                service_seconds,
                capture_seconds,
                self.bytes_sent,
                self.motion_band,
                self.motion_score,
                bytes_sent,
            )
        )
        while len(self.samples) > 2 and self.samples[1][0] < now - 1.2:
            self.samples.popleft()
        first = self.samples[0]
        duration = now - first[0]
        if duration > 0:
            self.display_fps = (displayed - first[1]) / duration
        costs = sorted(s[2] for s in self.samples)
        self.service_ms = costs[int((len(costs) - 1) * 0.8)] * 1000
        mean_service = sum(s[2] for s in self.samples) / len(self.samples)
        mean_capture = sum(s[3] for s in self.samples) / len(self.samples)
        self.capture_ms = mean_capture * 1000
        quick = next((s for s in self.samples if s[0] >= now - 0.4), first)
        self.short_fps = (
            (displayed - quick[1]) / (now - quick[0]) if now > quick[0] else self.display_fps
        )
        payload_rate = (self.bytes_sent - first[4]) * 8 / duration if duration else 0
        if duration < 0.3 or len(costs) < 4:
            return None
        if self.last_check is not None and now - self.last_check < 0.1:
            return None
        self.last_check = now
        budget = 1 / self.fps
        if mean_capture > budget and mean_service - mean_capture < budget * 0.65:
            self.good_since = self.bad_since = None
            self.reason = "desktop capture limited"
            return None
        if (
            self.moving
            and self.moving_since is not None
            and now - self.moving_since >= 1.2
            and (self.last_change is None or now - self.last_change >= 1.5)
            and self.display_fps >= self.fps * 0.97
            and mean_service < budget * 0.95
        ):
            self.safe_motion_bitrate = self.bitrate
            band = Counter(s[5] for s in self.samples).most_common(1)[0][0]
            if band:
                self.safe_rates[band] = self.bitrate
                self.safe_levels[band] = sum(s[6] for s in self.samples if s[5] == band) / sum(
                    s[5] == band for s in self.samples
                )
        # A slow USB reply can overlap useful decoder work. Do not lower quality
        # for that alone when recent displayed FPS is already on target.
        urgent = self.short_fps < self.fps * 0.65 or (
            self.motion_band > 0 and self.short_fps < self.fps * 0.78
        )
        overloaded = urgent or (duration >= 0.7 and self.display_fps < self.fps * 0.94)
        payloads = sorted(s[7] for s in self.samples)
        steady_light = (
            self.previous_image is not None
            and not self.moving
            and self.motion_score < 0.25
            and payloads[len(payloads) // 2] < 256
        )
        # Low-motion video can still be expensive. Brief headroom after a cut
        # should not repeat the same bitrate overshoot; tiny static frames can
        # recover quickly because their work is already cheap.
        recovering = (
            not steady_light and self.last_reduction is not None and now - self.last_reduction < 10
        )
        if steady_light and overloaded and (not urgent or self.protect_idle):
            # Tiny unchanged P frames cannot be made meaningfully cheaper.
            # Do not oscillate image quality in response to idle scheduler noise.
            self.good_since = self.bad_since = None
            self.reason = "frame timing limited"
            return None
        if overloaded:
            self.good_since = None
            if self.bad_since is None:
                self.bad_since = now
            # Brief 28-FPS dips and scene-transition spikes should not leave
            # the next easy scene unnecessarily blurry.
            emergency = urgent or self.display_fps < self.fps * 0.75
            if not emergency and now - self.bad_since < 0.9:
                return None
            if self.last_change is not None and now - self.last_change < (
                0.25 if emergency else 0.8
            ):
                return None
            ratio = (
                max(0.35, min(0.8, self.short_fps / self.fps * 0.85))
                if urgent
                else 0.65
                if emergency
                else 0.8
                if self.display_fps < self.fps * 0.9
                else 0.9
            )
            proposed = max(self.minimum, int(self.bitrate * ratio) // 1000 * 1000)
            self.reason = "reducing traffic" if proposed < self.bitrate else "quality floor reached"
        elif (
            duration >= 0.7
            and self.display_fps >= self.fps * 0.97
            and mean_service < budget * (0.75 if self.motion_band >= 2 or recovering else 0.9)
        ):
            self.bad_since = None
            if self.good_since is None:
                self.good_since = now
            if now - self.good_since < (2.5 if self.motion_band >= 2 or recovering else 0.75):
                return None
            factor = (
                1.1
                if self.motion_band >= 2 or recovering
                else 2.0
                if payload_rate < self.bitrate * 0.4 and mean_service < budget * 0.7
                else 1.5
                if mean_service < budget * 0.7
                else 1.2
            )
            proposed = min(self.maximum, int(self.bitrate * factor) // 1000 * 1000)
            self.reason = (
                "restoring detail" if proposed > self.bitrate else "quality ceiling reached"
            )
            self.good_since = now
        else:
            self.good_since = None
            self.bad_since = None
            self.reason = "holding quality"
            return None
        if proposed == self.bitrate:
            return None
        if proposed < self.bitrate:
            self.last_reduction = now
        self.bitrate = proposed
        self.last_change = now
        self.changes += 1
        return proposed


class AdaptiveMpeg4Quality:
    """Reuse motion/timing feedback with an inverse-Q budget, not a bitrate cap."""

    SCALE = 1_000_000

    def __init__(self, fps):
        self.quantizer = 1
        self.changes = self.predictions = 0
        self.unsafe = {}
        self.feedback = AdaptiveQuality(
            fps, self.SCALE, self.SCALE // 8, self.SCALE, protect_idle=True
        )

    def __getattr__(self, name):
        if name in ("reason", "motion_band", "motion_score", "capture_ms", "short_fps"):
            return getattr(self.feedback, name)
        raise AttributeError(name)

    def _apply(self, proposed, now, prediction=False):
        if proposed is None:
            return None
        budget = self.SCALE // self.quantizer
        q = max(
            1,
            min(
                8,
                math.ceil(self.SCALE / proposed)
                if proposed < budget
                else math.floor(self.SCALE / proposed),
            ),
        )
        band = self.feedback.motion_band
        if q > self.quantizer:
            self.unsafe[band] = (self.quantizer, self.feedback.motion_score, now)
        elif q < self.quantizer:
            failed = self.unsafe.get(band)
            payloads = sorted(sample[7] for sample in self.feedback.samples)
            cheap = payloads and payloads[len(payloads) // 2] < 256
            if (
                failed
                and q <= failed[0]
                and now - failed[2] < 10
                and not cheap
                and self.feedback.motion_score >= failed[1] * 0.75
            ):
                q = self.quantizer
                self.feedback.reason = "holding tested quality"
                self.feedback.good_since = now
        # Keep the feedback variable at the effective integer Q, avoiding drift
        # toward repeated tiny proposals that never change the encoded picture.
        self.feedback.bitrate = self.SCALE // q
        if q == self.quantizer:
            return None
        self.quantizer = q
        self.changes += 1
        self.predictions += int(prediction)
        return q

    def prepare(self, image, now):
        return self._apply(self.feedback.prepare(image, now), now, prediction=True)

    def observe(self, now, displayed, service_seconds, capture_seconds, bytes_sent=0):
        proposed = self.feedback.observe(
            now, displayed, service_seconds, capture_seconds, bytes_sent
        )
        if self.quantizer > 1 and self.feedback.reason == "frame timing limited":
            proposed = self.SCALE
            self.feedback.reason = "restoring detail on idle screen"
        return self._apply(proposed, now)
