"""Run with no arguments for the desktop window; --cli for terminal/Linux/Pi use."""

import argparse
import json
import multiprocessing as mp
import os
import secrets
import threading
import time
import statistics
import contextlib
import sys
from collections import deque
from pathlib import Path
from encoder import Encoder, test_image
from capture import create_capture, TimingLease
from transport import Bridge, linking_apps
from wire import (
    control,
    fragments,
    acknowledgement,
    HELLO,
    POLL,
    CAPS,
    CLOCK,
    INPUT,
    STATS,
    STREAM,
    DISPLAY_RATE,
    OK,
    BUSY,
    RESYNC,
    FRAME_LIMIT,
    PIXEL_LIMIT,
    BULK,
    CAP_BULK,
    CAP_RAW,
    CAP_PIXELS,
    CAP_CLOCK,
    CAP_INPUT,
    CAP_MPEG4,
    CAP_STATS,
    CAP_STREAM,
    CAP_SRAM_TABLES,
    CAP_DISPLAY_RATE,
)
from wire import CAP_PAUSE, ReceiverClosing
from remote_input import RemoteInput
from adaptive import AdaptiveQuality, AdaptiveMpeg4Quality
from scheduling import SchedulingLease
from pacing import next_deadline, wait_for_deadline
from clock_options import CLOCKS, valid_clock


class Resync(Exception):
    pass


def app_version():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return (root / "VERSION").read_text().strip()


class ReceiverUpgrade(Exception):
    pass


def exchange_control(bridge, kind, session, value=0, input_ack=0):
    with bridge.connection():
        return acknowledgement(bridge.exchange(control(kind, session, value, input_ack)), session)


def stream(args, stop, report=print, bridge_factory=Bridge, stats_report=None):
    from diagnostics import ConnectionLog

    diagnostic = ConnectionLog(
        getattr(args, "connection_log", None),
        capture_stream=getattr(args, "capture_failure_stream", False),
    )
    deliver = report

    def report(text):
        diagnostic.status(text)
        if diagnostic.notice:
            deliver(diagnostic.notice)
            diagnostic.notice = None
        deliver(text)

    diagnostic.record(
        "start",
        backend=args.backend or ("windows" if os.name == "nt" else "linux"),
        mode=getattr(args, "mode", "h264"),
        fps=args.fps,
        clock=getattr(args, "clock", 0),
        preset=getattr(args, "preset", "medium"),
        auto_quality=getattr(args, "auto_quality", False),
    )
    capture = None
    timer = None
    scheduling = None
    virtual = None
    fatal = None
    final_message = None
    frame_timings = None
    try:
        if getattr(args, "frame_metrics", None):
            from frame_timings import FrameTimings

            frame_timings = FrameTimings(args.frame_metrics)
        timer = TimingLease()
        scheduling = SchedulingLease()
        if getattr(args, "virtual_display", False):
            from virtual_display import VirtualDisplay, ensure_resolution

            size = tuple(getattr(args, "desktop_size", (480, 360)))
            borrowed = getattr(args, "display_lease", None)
            if borrowed:
                if borrowed.size != size or not borrowed.name:
                    raise OSError("The prepared virtual desktop does not match the requested size.")
                r = borrowed.region
            else:
                ensure_resolution(size, report)
                if stop.is_set():
                    return
                report("Creating the calculator display...")
                virtual = VirtualDisplay(size=size).open()
                r = virtual.region
            args = argparse.Namespace(**vars(args))
            args.follow_display = borrowed or virtual
            args.region = (r["left"], r["top"], r["width"], r["height"])
            args.capture = "gdi"
            report(
                f"Desktop ready: {size[0]} × {size[1]} → 320 × 240 on the calculator. Move a window here with Win+Shift+Arrow."
            )
        if not args.test_pattern:
            args = argparse.Namespace(**{**vars(args), "stop_event": stop})
            if getattr(args, "pipeline", False):
                from latest_capture import LatestCapture

                capture = LatestCapture(args, create_capture)
            else:
                capture = create_capture(args)
            report(capture.name)
        while not stop.is_set():
            bridge = None
            remote = None
            stream_channel = None
            caps = 0
            session = 0
            diagnostic.new_session()
            state = {}
            receiver_closing = False
            try:
                report("Connecting to the calculator...")
                while linking_apps() and not stop.is_set():
                    report("Close TI-Nspire PC software completely, then mirroring will connect.")
                    stop.wait(0.5)
                if stop.is_set():
                    break
                bridge = bridge_factory(stop, args.backend, args.library)
                report("Waiting for the calculator USB connection...")
                session = secrets.randbits(32) or 1
                state = exchange_control(bridge, HELLO, session)
                if state["status"] != OK:
                    raise Resync("Calculator is finishing its previous frame")
                features = exchange_control(bridge, CAPS, session)
                caps = features["received"] if features["status"] == OK else 0
                if not caps & CAP_DISPLAY_RATE:
                    diagnostic.record(
                        "receiver_upgrade",
                        message="Update ndmirror.tns for wall-clock FPS on the calculator",
                    )
                clock = getattr(args, "clock", 0)
                if clock and not caps & CAP_CLOCK:
                    raise ReceiverUpgrade("Clock control needs the updated ndmirror.tns")
                if caps & CAP_CLOCK:
                    changed = exchange_control(bridge, CLOCK, session, clock)
                    if changed["status"] != OK:
                        raise ReceiverUpgrade("The receiver rejected that clock setting")
                    for tries in range(30):
                        if stop.wait(0.05):
                            raise InterruptedError("Stopped")
                        features = exchange_control(bridge, CAPS, session)
                        if features["status"] != BUSY:
                            break
                    if features["status"] != OK or clock and features["rejected"] != clock:
                        raise ReceiverUpgrade("Calculator could not apply the selected clock")
                limit = min(state["limit"], args.packet_size or state["limit"])
                actual_clock = features.get("rejected") if caps & CAP_CLOCK else None
                sram_interval = getattr(args, "sram_ab_seconds", 0)
                table_mode = getattr(args, "sram_mode", None)
                if sram_interval and not caps & CAP_SRAM_TABLES:
                    raise ReceiverUpgrade("The SRAM comparison needs its experimental receiver")
                if getattr(args, "input", False):
                    if not caps & CAP_INPUT:
                        raise ReceiverUpgrade(
                            "Calculator controls need the new ndmirror.tns; replace it or turn control off."
                        )
                    region = getattr(capture, "desktop_region", None) or getattr(
                        capture, "region", None
                    )
                    if region is None and os.name != "nt" and args.test_pattern:
                        region = dict(left=0, top=0, width=320, height=240)
                    if region is None:
                        import mss

                        with getattr(mss, "MSS", mss.mss)() as reader:
                            if args.region:
                                x, y, width, height = args.region
                                region = dict(left=x, top=y, width=width, height=height)
                            else:
                                region = reader.monitors[args.monitor]
                    remote = RemoteInput(
                        region,
                        getattr(args, "pointer_gain", 1.0),
                        confine=getattr(args, "confine_pointer", False),
                        reverse_scroll=getattr(args, "reverse_scroll", False),
                        scroll_gain=getattr(args, "scroll_gain", 1.0),
                        keyboard_layout=getattr(args, "keyboard_layout", "auto"),
                    )
                    enabled = exchange_control(bridge, INPUT, session, 1)
                    if enabled["status"] != OK or enabled["reply_bytes"] == 32:
                        raise ReceiverUpgrade("Receiver could not enable calculator controls")
                    remote.apply(enabled["events"])
                    report("Calculator mouse and keyboard control enabled")
                mode = getattr(args, "mode", "h264")
                adaptive = (
                    (
                        AdaptiveMpeg4Quality(args.fps)
                        if mode == "mpeg4"
                        else AdaptiveQuality(args.fps)
                    )
                    if mode in ("h264", "mpeg4") and getattr(args, "auto_quality", False)
                    else None
                )
                if mode == "mpeg4":
                    if not caps & CAP_MPEG4:
                        raise ReceiverUpgrade("MPEG-4 needs the new ndmirror.tns")
                    encoder = Encoder(
                        args.fps,
                        args.crf,
                        mode="mpeg4",
                        quantizer=adaptive.quantizer if adaptive else getattr(args, "mpeg4_q", 1),
                        preset=getattr(args, "preset", "medium"),
                    )
                elif mode != "h264":
                    needed = CAP_RAW if mode == "raw" else CAP_PIXELS
                    if not caps & needed:
                        raise ReceiverUpgrade("Lossless/raw mode needs the updated ndmirror.tns")
                    from pixels import PixelEncoder

                    encoder = PixelEncoder(raw=mode == "raw")
                else:
                    encoder = Encoder(
                        args.fps,
                        args.crf,
                        preset=getattr(args, "preset", "medium"),
                        key_seconds=getattr(args, "keyframe_seconds", 30),
                        bitrate=adaptive.bitrate if adaptive else None,
                    )
                if getattr(args, "profile", False):
                    if not caps & CAP_STATS:
                        raise ReceiverUpgrade("Receiver timing needs the profile5 ndmirror.tns")
                    measured = exchange_control(
                        bridge, STATS, session, 1, input_ack=remote.ack if remote else 0
                    )
                    if measured["status"] != OK or not measured.get("profile"):
                        raise IOError("Could not enable receiver timings")
                persistent = bool(getattr(args, "persistent", False))
                if persistent:
                    if not caps & CAP_STREAM:
                        raise ReceiverUpgrade(
                            "Persistent USB transfer needs the stream6 ndmirror.tns"
                        )
                    channel = bridge.connection()
                    channel.__enter__()
                    stream_channel = channel
                    state = acknowledgement(
                        bridge.exchange(
                            control(STREAM, session, input_ack=remote.ack if remote else 0)
                        ),
                        session,
                    )
                    if state["status"] != OK:
                        raise IOError("Receiver rejected persistent USB transfer")

                def channel_control(kind, value=0):
                    if persistent:
                        return acknowledgement(
                            bridge.exchange(
                                control(kind, session, value, input_ack=remote.ack if remote else 0)
                            ),
                            session,
                        )
                    return exchange_control(
                        bridge, kind, session, value, input_ack=remote.ack if remote else 0
                    )

                if table_mode:
                    if not caps & CAP_SRAM_TABLES:
                        if table_mode != "ram":
                            raise ReceiverUpgrade("SRAM table mode needs the experimental receiver")
                    else:
                        selected = channel_control(STATS, 3 if table_mode == "ram" else 4)
                        actual = selected.get("profile", {}).get("sram_tables", {}).get("enabled")
                        if selected["status"] != OK or actual != (table_mode == "sram"):
                            raise IOError("Receiver did not apply the requested table mode")
                        diagnostic.record(
                            "table_mode",
                            session=session,
                            mode=table_mode,
                            clock_mhz=actual_clock,
                            profile=selected["profile"],
                        )
                        report(f"Colour tables: {table_mode.upper()}")

                sent = 0
                byte_count = 0
                app_bytes = 0
                packet_count = 0
                capture_seconds = encode_seconds = transfer_seconds = credit_seconds = 0.0
                connect_seconds = wire_seconds = disconnect_seconds = 0.0
                frame_times = deque(maxlen=512)
                capture_age_seconds = 0.0
                input_at = 0.0
                # Use the same precise counter as pacing and LatestCapture.
                # Python 3.12 Windows monotonic() advances in 15.6 ms steps.
                started = time.perf_counter()
                cpu_started = time.process_time()
                sram_phase = 0
                sram_at = started
                if sram_interval:
                    tables = channel_control(STATS, 3)
                    if tables["status"] != OK or not tables.get("profile", {}).get("sram_tables"):
                        raise ReceiverUpgrade("Receiver did not acknowledge RAM table mode")
                    diagnostic.record(
                        "sram_phase",
                        session=session,
                        phase=0,
                        enabled=False,
                        clock_mhz=actual_clock,
                        profile=tables["profile"],
                    )
                    if frame_timings:
                        frame_timings.write(
                            dict(
                                kind="sram_phase",
                                session=session,
                                phase=0,
                                enabled=False,
                                elapsed=0,
                                profile=tables["profile"],
                            )
                        )
                    report("Table comparison: RAM phase 1")
                sample = started
                timing_at = started
                timing_totals = (0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                pacing_seconds = wake_late_seconds = 0.0
                next_capture = started
                recent = deque()
                previous_capture = previous_ack = None
                profile_at = started
                report(
                    f"Connected / {mode} / 320 x 240 / {'persistent' if persistent else 'batched' if caps & CAP_BULK else 'legacy'} transfer"
                )
                while not stop.is_set():
                    credit_before = credit_seconds
                    # At most two encoded frames in flight; do not accumulate captured frames.
                    while not state["credit"] and not stop.is_set():
                        waiting = time.perf_counter()
                        if stop.wait(0.005):
                            break
                        state = channel_control(POLL)
                        if remote:
                            before = remote.ack
                            remote.apply(state["events"])
                            if remote.ack > before:
                                input_at = time.perf_counter()
                        credit_seconds += time.perf_counter() - waiting
                        app_bytes += 32 + state["reply_bytes"]
                        packet_count += 1
                        if state["status"] == RESYNC:
                            raise Resync("Calculator requested a fresh keyframe")
                        if state["status"] not in (OK, BUSY):
                            raise IOError("Calculator rejected stream poll")
                    if stop.is_set():
                        break
                    pacing_started = time.perf_counter()
                    capture_due = next_capture
                    wait_for_deadline(stop, next_capture, remote.tick if remote else None)
                    if stop.is_set():
                        break
                    if remote:
                        remote.tick()
                    captured = time.perf_counter()
                    service_started = captured
                    pacing_wait = captured - pacing_started
                    pacing_seconds += pacing_wait
                    wake_late_seconds += max(0, service_started - capture_due)
                    if args.test_pattern:
                        image = test_image(sent)
                    else:
                        image = (
                            capture.grab(after=input_at)
                            if getattr(args, "pipeline", False)
                            else capture.grab()
                        )
                    captured_image = time.perf_counter()
                    capture_luma = None
                    if frame_timings:
                        # Numeric-only source evidence distinguishes a black
                        # capture from receiver/display recovery. Tiny C-side
                        # downsampling keeps this opt-in instrumentation cheap.
                        from PIL import ImageStat

                        luma = image.resize((16, 12)).convert("L")
                        capture_luma = (ImageStat.Stat(luma).mean[0], luma.getextrema()[1])
                    capture_seconds += captured_image - captured
                    if getattr(args, "pipeline", False) and not args.test_pattern:
                        capture_seconds -= captured_image - captured
                        _, captured, capture_finished = capture.last_sample
                        capture_seconds += capture_finished - captured
                        capture_age_seconds += captured_image - capture_finished
                    if adaptive:
                        predicted = adaptive.prepare(image, time.perf_counter())
                        if predicted is not None:
                            encoder.set_quality(predicted)
                    output = encoder.encode(image)
                    data, key = output[:2]
                    pixel_flags = output[2] if len(output) > 2 else 0
                    encoded = time.perf_counter()
                    encode_seconds += encoded - captured_image
                    if len(data) > (PIXEL_LIMIT if mode in ("lossless", "raw") else FRAME_LIMIT):
                        raise ValueError("Frame exceeds its receiver buffer limit")
                    sequence = sent + 1
                    if sequence >= 0xFFFFFFFF:
                        raise Resync("Starting a new stream sequence")
                    diagnostic.encoded(sequence, key, data if mode == "h264" else None)
                    # The channel is retained for every fragment of this frame.
                    with contextlib.nullcontext(bridge) if persistent else bridge.connection():
                        connected = time.perf_counter()
                        connect_seconds += connected - encoded
                        packets = list(
                            fragments(
                                session,
                                sequence,
                                data,
                                key,
                                limit,
                                pixel_flags | (BULK if caps & CAP_BULK else 0),
                                input_ack=remote.ack if remote else 0,
                            )
                        )
                        if caps & CAP_BULK:
                            state = acknowledgement(bridge.batch(packets), session)
                            diagnostic.frame(sequence, len(data), key, state)
                            app_bytes += sum(map(len, packets)) + state["reply_bytes"]
                            packet_count += len(packets)
                            if remote:
                                before = remote.ack
                                remote.apply(state["events"])
                                if remote.ack > before:
                                    input_at = time.perf_counter()
                            if state["status"] == RESYNC:
                                raise Resync("Calculator requested a fresh keyframe")
                            if state["status"] != OK:
                                raise IOError(f"Frame rejected (status {state['status']})")
                        else:
                            for packet in packets:
                                state = acknowledgement(bridge.exchange(packet), session)
                                app_bytes += len(packet) + state["reply_bytes"]
                                packet_count += 1
                                if remote:
                                    before = remote.ack
                                    remote.apply(state["events"])
                                    if remote.ack > before:
                                        input_at = time.perf_counter()
                                if state["status"] == RESYNC:
                                    raise Resync("Calculator requested a fresh keyframe")
                                if state["status"] != OK:
                                    raise IOError(f"Frame rejected (status {state['status']})")
                        acknowledged = time.perf_counter()
                        wire_seconds += acknowledged - connected
                    if state["accepted"] != sequence:
                        raise IOError("Frame was not fully acknowledged")
                    sent += 1
                    byte_count += len(data)
                    now = time.perf_counter()
                    transfer_seconds += now - encoded
                    disconnect_seconds += now - acknowledged
                    frame_times.append((now - captured) * 1000)
                    if frame_timings:
                        frame_timings.write(
                            dict(
                                kind="frame",
                                session=session,
                                frame=sent,
                                elapsed=now - started,
                                clock_mhz=actual_clock,
                                target_fps=args.fps,
                                keyframe=bool(key),
                                bytes=len(data),
                                packets=len(packets),
                                capture_ms=(captured_image - service_started) * 1000,
                                encode_ms=(encoded - captured_image) * 1000,
                                rpc_transfer_ms=(acknowledged - connected) * 1000,
                                credit_wait_ms=(credit_seconds - credit_before) * 1000,
                                pacing_wait_ms=pacing_wait * 1000,
                                wake_late_ms=max(0, service_started - capture_due) * 1000,
                                capture_interval_ms=None
                                if previous_capture is None
                                else (service_started - previous_capture) * 1000,
                                ack_interval_ms=None
                                if previous_ack is None
                                else (acknowledged - previous_ack) * 1000,
                                capture_to_ack_ms=(acknowledged - service_started) * 1000,
                                displayed=state["displayed"],
                                queued=state["accepted"] - state["displayed"],
                                bitrate=adaptive.bitrate if adaptive and mode == "h264" else None,
                                mpeg4_q=encoder.quantizer if mode == "mpeg4" else None,
                                motion_score=adaptive.motion_score if adaptive else None,
                                capture_luma_mean=capture_luma[0],
                                capture_luma_max=capture_luma[1],
                            )
                        )
                    previous_capture, previous_ack = service_started, acknowledged
                    if adaptive:
                        changed = adaptive.observe(
                            now,
                            state["displayed"],
                            now - service_started + credit_seconds - credit_before,
                            captured_image - service_started,
                            len(data),
                            encode_seconds=encoded - captured_image,
                            queued=state["accepted"] - state["displayed"],
                            keyframe=bool(key),
                        )
                        if changed is not None:
                            encoder.set_quality(changed)
                    recent.append((now, state["displayed"], byte_count))
                    while len(recent) > 2 and recent[1][0] < now - 1.5:
                        recent.popleft()
                    # Keep an absolute capture cadence. Windows event waits
                    # can round up to a scheduler tick; restarting the period
                    # from each rounded wake-up reduced a 20 FPS target to 16.
                    next_capture = next_deadline(next_capture, now, args.fps)
                    if getattr(args, "profile", False) and now - profile_at >= 5.3:
                        profiled = channel_control(STATS, 0)
                        if frame_timings:
                            frame_timings.write(
                                dict(
                                    kind="receiver_profile",
                                    session=session,
                                    elapsed=time.perf_counter() - started,
                                    profile=profiled.get("profile"),
                                )
                            )
                        profile_at = now
                    if sram_interval and now - sram_at >= sram_interval:
                        deadline = time.perf_counter() + 3
                        while state["displayed"] < state["accepted"]:
                            state = channel_control(POLL)
                            if state["status"] != OK:
                                raise Resync(
                                    "Receiver requested a reset at the table comparison boundary"
                                )
                            if time.perf_counter() > deadline:
                                raise IOError("Receiver did not finish the table comparison phase")
                            if stop.wait(0.001):
                                raise InterruptedError("Stopped")
                        sram_phase += 1
                        if sram_phase >= getattr(args, "sram_ab_phases", 8):
                            final_tables = channel_control(STATS, 0)
                            diagnostic.record(
                                "sram_complete",
                                session=session,
                                profile=final_tables.get("profile"),
                            )
                            if frame_timings:
                                frame_timings.write(
                                    dict(
                                        kind="receiver_profile",
                                        session=session,
                                        elapsed=time.perf_counter() - started,
                                        profile=final_tables.get("profile"),
                                    )
                                )
                            report(
                                "Table comparison complete. Stop the receiver normally to save its log."
                            )
                            stop.set()
                            break
                        enabled = bool(sram_phase % 2)
                        tables = channel_control(STATS, 4 if enabled else 3)
                        if (
                            tables["status"] != OK
                            or tables.get("profile", {}).get("sram_tables", {}).get("enabled")
                            != enabled
                        ):
                            raise IOError("Receiver did not apply the requested SRAM table mode")
                        sram_at = time.perf_counter()
                        diagnostic.record(
                            "sram_phase",
                            session=session,
                            phase=sram_phase,
                            enabled=enabled,
                            clock_mhz=actual_clock,
                            profile=tables["profile"],
                        )
                        if frame_timings:
                            frame_timings.write(
                                dict(
                                    kind="sram_phase",
                                    session=session,
                                    phase=sram_phase,
                                    enabled=enabled,
                                    elapsed=sram_at - started,
                                    profile=tables["profile"],
                                )
                            )
                        report(
                            f"Table comparison: {'SRAM' if enabled else 'RAM'} phase {sram_phase + 1}"
                        )
                    if now - sample >= 1:
                        elapsed = now - started
                        window = now - recent[0][0]
                        displayed_fps = (
                            (state["displayed"] - recent[0][1]) / window if window else 0.0
                        )
                        recent_kbps = (
                            (byte_count - recent[0][2]) * 8 / window / 1000 if window else 0.0
                        )
                        if caps & CAP_DISPLAY_RATE:
                            rate_state = channel_control(
                                DISPLAY_RATE, min(1000, round(displayed_fps * 10))
                            )
                            if rate_state["status"] != OK:
                                raise OSError("Receiver rejected the display FPS update")
                            state = rate_state
                            if remote:
                                before = remote.ack
                                remote.apply(state["events"])
                                if remote.ack > before:
                                    input_at = time.perf_counter()
                        quality_info = (
                            (
                                f" / auto Q{adaptive.quantizer}"
                                if mode == "mpeg4"
                                else f" / auto {adaptive.bitrate // 1000} kbps"
                            )
                            if adaptive
                            else ""
                        )
                        report(
                            f"{displayed_fps:.1f} FPS displayed / {recent_kbps:.0f} kbps{quality_info} / latest frame {1000 * (now - captured):.0f} ms capture-to-ack"
                        )
                        if now - timing_at >= 5:
                            totals = (
                                sent,
                                capture_seconds,
                                encode_seconds,
                                wire_seconds,
                                credit_seconds,
                                pacing_seconds,
                                wake_late_seconds,
                            )
                            count = sent - timing_totals[0]
                            costs = [
                                (value - old) * 1000 / count
                                for value, old in zip(totals[1:], timing_totals[1:])
                            ]
                            diagnostic.record(
                                "performance",
                                session=session,
                                mode=mode,
                                target_fps=args.fps,
                                receiver=diagnostic.receiver(state),
                                displayed_fps=displayed_fps,
                                payload_kbps=recent_kbps,
                                clock_mhz=actual_clock,
                                auto_quality=adaptive is not None,
                                mpeg4_q=encoder.quantizer if mode == "mpeg4" else None,
                                adaptive_reason=adaptive.reason if adaptive else None,
                                capture_ms=costs[0],
                                encode_ms=costs[1],
                                usb_ms=costs[2],
                                credit_wait_ms=costs[3],
                                pacing_wait_ms=costs[4],
                                wake_late_ms=costs[5],
                            )
                            timing_at, timing_totals = now, totals
                        if stats_report:
                            stats_report(
                                dict(
                                    elapsed=elapsed,
                                    host_cpu_seconds=time.process_time() - cpu_started,
                                    frames=sent,
                                    displayed=state["displayed"],
                                    fps=sent / elapsed,
                                    payload_bytes=byte_count,
                                    application_bytes=app_bytes,
                                    packets=packet_count,
                                    payload_kbps=byte_count * 8 / elapsed / 1000,
                                    application_kbps=app_bytes * 8 / elapsed / 1000,
                                    capture_ms=capture_seconds * 1000 / sent,
                                    encode_ms=encode_seconds * 1000 / sent,
                                    transfer_ms=transfer_seconds * 1000 / sent,
                                    credit_wait_ms=credit_seconds * 1000 / sent,
                                    queued=state["accepted"] - state["displayed"],
                                    crf=args.crf,
                                    target_fps=args.fps,
                                    mode=mode,
                                    batched=bool(caps & CAP_BULK),
                                    displayed_fps_recent=displayed_fps,
                                    payload_kbps_recent=recent_kbps,
                                    auto_quality=adaptive is not None,
                                    adaptive_bitrate=adaptive.bitrate
                                    if adaptive and mode == "h264"
                                    else None,
                                    adaptive_reason=adaptive.reason if adaptive else None,
                                    capacity_bitrate=getattr(adaptive, "capacity_bitrate", None),
                                    service_ms=getattr(adaptive, "service_ms", None),
                                    controller_phase=getattr(adaptive, "phase", None),
                                    capacity_confidence=getattr(
                                        getattr(adaptive, "budget_model", None), "confidence", None
                                    ),
                                    adaptive_changes=adaptive.changes if adaptive else 0,
                                    adaptive_predictions=adaptive.predictions if adaptive else 0,
                                    safe_motion_bitrate=adaptive.safe_motion_bitrate
                                    if adaptive and mode == "h264"
                                    else None,
                                    motion_band=adaptive.motion_band if adaptive else None,
                                    motion_score=adaptive.motion_score if adaptive else None,
                                    capture_ms_recent=adaptive.capture_ms if adaptive else None,
                                    displayed_fps_short=adaptive.short_fps if adaptive else None,
                                    input_enabled=remote is not None,
                                    input_sequence=remote.ack if remote else 0,
                                    mpeg4_q=encoder.quantizer if mode == "mpeg4" else None,
                                    preset=getattr(args, "preset", "ultrafast"),
                                    connection_ms=connect_seconds * 1000 / sent,
                                    wire_ms=wire_seconds * 1000 / sent,
                                    disconnect_ms=disconnect_seconds * 1000 / sent,
                                    capture_to_ack_median_ms=statistics.median(frame_times),
                                    capture_to_ack_p95_ms=sorted(frame_times)[
                                        int((len(frame_times) - 1) * 0.95)
                                    ],
                                    pipelined_capture=bool(getattr(args, "pipeline", False)),
                                    persistent_channel=persistent,
                                    capture_age_ms=capture_age_seconds * 1000 / sent,
                                )
                            )
                        sample = now
                    if getattr(args, "duration", 0) and now - started >= args.duration:
                        if getattr(args, "profile", False):
                            measured = channel_control(STATS, 2)
                            if stats_report:
                                stats_report(
                                    dict(
                                        receiver_profile=measured["profile"],
                                        host_elapsed=time.perf_counter() - started,
                                    )
                                )
                        stop.set()
            except InterruptedError:
                break
            except ReceiverClosing:
                receiver_closing = True
                final_message = "Calculator receiver closed. Reopen ndmirror.tns to reconnect."
                return
            except ReceiverUpgrade as e:
                final_message = str(e)
                return
            except Exception as e:
                if stop.is_set():
                    break
                diagnostic.failure(e, session, state)
                report(f"{e}. Retrying...")
                if stop.wait(1):
                    break
            finally:
                try:
                    if remote:
                        try:
                            remote.close()
                        except OSError as e:
                            report(str(e))
                finally:
                    try:
                        if bridge and stream_channel and caps & CAP_PAUSE and not receiver_closing:
                            try:
                                bridge.pause_session(session, remote.ack if remote else 0)
                            except ReceiverClosing:
                                pass
                            except Exception as e:
                                report("USB stream cleanup: " + str(e))
                        if stream_channel:
                            try:
                                stream_channel.__exit__(None, None, None)
                            except Exception as e:
                                # A failed native channel close invalidates this
                                # bridge, but must not escape the session retry
                                # loop and be mislabeled as a capture failure.
                                diagnostic.failure(e, session, state)
                                report("USB channel cleanup: " + str(e))
                    finally:
                        if bridge:
                            bridge.close()
    except Exception as e:
        diagnostic.failure(e, 0, {})
        fatal = str(e)
    finally:
        try:
            if capture:
                capture.close()
        finally:
            if virtual:
                try:
                    virtual.close()
                except OSError as error:
                    report("Calculator display could not be detached: " + str(error))
            if timer:
                timer.close()
            if scheduling:
                scheduling.close()
            if frame_timings:
                frame_timings.close()
        report(final_message or ("Mirroring stopped: " + fatal if fatal else "Stopped"))


def gui(defaults, remember=False, settings_path=None):
    from desktop import gui as desktop_gui
    from bridge_pool import BridgePool
    from functools import partial

    pool = BridgePool()
    try:
        desktop_gui(
            defaults,
            partial(stream, bridge_factory=pool),
            validate,
            remember=remember,
            settings_path=settings_path,
        )
    finally:
        pool.close()


def validate(args):
    if getattr(args, "keyboard_layout", "auto") not in ("auto", "us", "fr"):
        raise ValueError("Keyboard layout must be auto, us or fr")
    if not valid_clock(getattr(args, "clock", 0)):
        raise ValueError("Clock must be 0, or CX II 396–492 / CX 132–240 MHz in steps of 12")
    if os.name != "nt" and getattr(args, "confine_pointer", False):
        raise ValueError("Pointer confinement is only available on Windows; turn it off on Linux")
    interval = getattr(args, "sram_ab_seconds", 0)
    if interval and getattr(args, "sram_mode", None):
        raise ValueError("Choose a fixed table mode or an A/B comparison, not both")
    if interval and (
        not 5 <= interval <= 1200
        or not getattr(args, "profile", False)
        or getattr(args, "mode", "h264") != "h264"
    ):
        raise ValueError(
            "SRAM comparison needs H.264, --profile, and a phase duration of 5–1200 seconds"
        )
    if not 2 <= getattr(args, "sram_ab_phases", 8) <= 32:
        raise ValueError("SRAM comparison must have 2–32 phases")
    if not 1 <= getattr(args, "keyframe_seconds", 30) <= 600:
        raise ValueError("Keyframe interval must be 1–600 seconds")
    if not 1 <= args.fps <= 60:
        raise ValueError("Frame rate must be 1–60")
    if not 8 <= args.crf <= 35:
        raise ValueError("CRF must be 8–35")
    if args.monitor < 0:
        raise ValueError("Monitor must be 0 or greater")
    if getattr(args, "virtual_display", False) and os.name != "nt":
        raise ValueError("The calculator virtual display requires Windows.")
    from virtual_display import validate_size

    validate_size(getattr(args, "desktop_size", (320, 240)))
    if getattr(args, "virtual_display", False) and args.region:
        raise ValueError(
            "The calculator virtual display uses its complete 320×240 screen; remove the custom region."
        )
    if args.region and (args.region[2] <= 0 or args.region[3] <= 0):
        raise ValueError("Capture region must have positive dimensions")
    if not 0.1 <= getattr(args, "pointer_gain", 1) <= 4:
        raise ValueError("Pointer sensitivity must be 0.1–4")
    if not 0.1 <= getattr(args, "scroll_gain", 1) <= 4:
        raise ValueError("Scroll sensitivity must be 0.1–4")
    if not 1 <= getattr(args, "mpeg4_q", 3) <= 8:
        raise ValueError("MPEG-4 quality must be 1–8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keyframe-seconds",
        type=int,
        default=30,
        help="Developer H.264 keyframe interval; reconnect always starts with an IDR",
    )
    parser.add_argument(
        "--frame-metrics",
        type=Path,
        help="Developer per-frame timings; no pixels or input contents",
    )
    parser.add_argument("--version", action="version", version="ND Screen Mirror " + app_version())
    parser.add_argument("--cli", action="store_true")
    parser.add_argument(
        "--diagnostic-log",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Save local connection/performance diagnostics; disabled by default",
    )
    parser.add_argument("--test-pattern", action="store_true")
    parser.add_argument(
        "--start", action="store_true", help="Start mirroring when the desktop window opens"
    )
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--crf", type=int, default=14)
    parser.add_argument(
        "--auto-quality",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Adapt H.264 bitrate or MPEG-4 Q to maintain FPS; --no-auto-quality uses fixed CRF/Q",
    )
    parser.add_argument(
        "--preset",
        choices=["ultrafast", "veryfast", "fast", "medium"],
        default="medium",
        help="PC encoding effort; codec remains Baseline with one reference and no B frames",
    )
    parser.add_argument(
        "--pipeline",
        action="store_true",
        help="Overlap desktop capture with USB; retain only the newest capture",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Enable experimental read-only receiver timing diagnostics",
    )
    parser.add_argument(
        "--sram-ab-seconds",
        type=float,
        default=0,
        help="Diagnostic: alternate RAM/SRAM table conversion every N seconds",
    )
    parser.add_argument(
        "--sram-mode",
        choices=["ram", "sram"],
        help="Diagnostic: hold the experimental receiver in one colour-table mode",
    )
    parser.add_argument(
        "--sram-ab-phases",
        type=int,
        default=8,
        help="Diagnostic: number of RAM/SRAM comparison phases before stopping",
    )
    parser.add_argument(
        "--persistent",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep one USB channel across frames; requires the matching receiver. --no-persistent uses one channel per frame for comparison",
    )
    parser.add_argument("--monitor", type=int, default=1)
    parser.add_argument("--region", type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    parser.add_argument(
        "--virtual-display",
        action="store_true",
        help="Create and capture a dedicated Windows display, scaled to 320×240; requires driver setup",
    )
    parser.add_argument(
        "--desktop-size",
        type=int,
        nargs=2,
        default=(480, 360),
        metavar=("WIDTH", "HEIGHT"),
        help="Virtual desktop resolution; 480 360 by default, scaled to the calculator",
    )
    parser.add_argument(
        "--setup-virtual-display",
        action="store_true",
        help="Open the GUI and begin one-time virtual display driver setup",
    )
    parser.add_argument(
        "--capture",
        choices=["auto", "dxgi", "gdi", "mss", "wayland"],
        help="Auto selects DXGI on Windows, the portal on Wayland and MSS on X11",
    )
    parser.add_argument("--mode", choices=["h264", "mpeg4", "lossless", "raw"], default="h264")
    parser.add_argument(
        "--mpeg4-q",
        type=int,
        choices=range(1, 9),
        default=1,
        help="MPEG-4 quantizer, 1–8; lower = sharper, more traffic",
    )
    parser.add_argument(
        "--no-input",
        action="store_false",
        dest="input",
        default=True,
        help="Mirror without forwarding calculator mouse/keyboard input",
    )
    parser.add_argument(
        "--keyboard-layout",
        choices=["auto", "us", "fr"],
        default="auto",
        help="Linux host letter layout: auto uses XKB_DEFAULT_LAYOUT (US fallback), us or fr",
    )
    parser.add_argument(
        "--pointer-gain", type=float, default=1.0, help="Touchpad sensitivity, 0.1–4; default 1"
    )
    parser.add_argument(
        "--reverse-scroll",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Reverse the default scroll direction, where content follows your swipe",
    )
    parser.add_argument(
        "--scroll-gain",
        type=float,
        default=1.0,
        help="Scroll sensitivity multiplier, 0.1–4; default 1",
    )
    parser.add_argument(
        "--confine-pointer",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Keep calculator-controlled pointer on the mirrored display; off by default",
    )
    parser.add_argument(
        "--clock",
        type=int,
        choices=CLOCKS,
        default=0,
        help="Calculator MHz; 0 restores its entry clock",
    )
    parser.add_argument("--backend", choices=["windows", "linux", "navnet", "libnspire"])
    parser.add_argument("--library")
    parser.add_argument(
        "--capture-failure-stream",
        action="store_true",
        help="Diagnostic: save the current H.264 GOP locally if the stream fails",
    )
    parser.add_argument(
        "--packet-size",
        type=int,
        choices=[254, 1440],
        help="Explicit message size override for diagnosis",
    )
    parser.add_argument(
        "--sample",
        type=Path,
        help="Encode a 3-second test stream locally; does not open USB or capture the screen",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Check native helper/library initialization without opening a calculator channel",
    )
    parser.add_argument(
        "--metrics",
        type=Path,
        help="Append timing/traffic metrics as JSON lines; no screen content is saved",
    )
    parser.add_argument(
        "--capture-probe",
        action="store_true",
        help="Check desktop capture/encoding dependencies without opening USB",
    )
    args = parser.parse_args()
    validate(args)
    if args.sample:
        codec = Encoder(args.fps, args.crf, preset=args.preset)
        with args.sample.open("wb") as f:
            for i in range(args.fps * 3):
                f.write(codec.encode(test_image(i))[0])
        print(args.sample.resolve())
        return
    if args.capture_probe:
        camera = create_capture(args)
        try:
            image = camera.grab()
            data, key = Encoder(args.fps, args.crf, preset=args.preset).encode(image)
            from pixels import PixelEncoder

            pixel, key, flags = PixelEncoder().encode(image)
            print(
                f"{camera.name}: image {image.size}; H264 {len(data)} bytes; lossless {len(pixel)} bytes"
            )
        finally:
            camera.close()
        return
    if args.probe:
        bridge = Bridge(threading.Event(), args.backend, args.library)
        bridge.close()
        print("Native USB helper initialized and closed; calculator connection was not attempted.")
        return
    from diagnostics import log_path

    args.connection_log = (
        log_path()
        if args.diagnostic_log
        or args.metrics
        or args.frame_metrics
        or args.capture_failure_stream
        or args.profile
        else None
    )
    if not args.cli:
        gui(args, remember=True)
        return
    stop = threading.Event()

    def record_stats(data):
        if args.metrics:
            with args.metrics.open("a") as f:
                f.write(json.dumps(data) + "\n")

    try:
        stream(args, stop, stats_report=record_stats if args.metrics else None)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    mp.freeze_support()
    main()
