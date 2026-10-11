"""One complete Annex B access unit per frame, fragmented across NavNet messages."""

import struct

HEADER = struct.Struct("<4sBBBBIIIIHHI")
ACK = struct.Struct("<4sBBBBIIIIII")
MAX_REPLY = 360  # ACK + the largest supported, bounded receiver timing record.
SERVICE = 0x4008
FRAME_LIMIT = 65536
PIXEL_LIMIT = 163840
HELLO, DATA, POLL, RESET, CAPS, PROBE, CLOCK, INPUT, STATS, STREAM = range(1, 11)
PAUSE = 11
DISPLAY_RATE = 12
OK, BUSY, BAD, RESYNC, CLOSING = range(5)
FIRST, LAST, KEY, BULK, RAW, DELTA, LZ4, MPEG4 = 1, 2, 4, 8, 16, 32, 64, 128
CAP_BULK, CAP_PROBE, CAP_RAW, CAP_PIXELS, CAP_CLOCK, CAP_INPUT, CAP_MPEG4, CAP_CX2 = (
    1,
    2,
    4,
    8,
    16,
    32,
    64,
    256,
)
CAP_STATS = 512
CAP_STREAM = 1024
CAP_PAUSE = 2048
CAP_SRAM_TABLES = 4096
CAP_DISPLAY_RATE = 8192
CAP_PAUSE_PEER_CLOSE = 16384
EVENT = struct.Struct("<IBBHhh")


class ReceiverClosing(Exception):
    pass


def control(kind, session, value=0, input_ack=0):
    return HEADER.pack(b"NDM1", 1, kind, 0, 0, session, value, 0, 0, 0, 0, input_ack)


def fragments(session, sequence, data, key, limit, extra_flags=0, kind=DATA, input_ack=0):
    maximum = PIXEL_LIMIT if extra_flags & (RAW | DELTA) or kind == PROBE else FRAME_LIMIT
    if not 0 < len(data) <= maximum or not 33 <= limit <= 1440:
        raise ValueError("Invalid frame or negotiated packet size")
    size = limit - HEADER.size
    for offset in range(0, len(data), size):
        piece = data[offset : offset + size]
        flags = (
            (FIRST if not offset else 0)
            | (LAST if offset + len(piece) == len(data) else 0)
            | (KEY if key else 0)
            | extra_flags
        )
        yield (
            HEADER.pack(
                b"NDM1",
                1,
                kind,
                flags,
                0,
                session,
                sequence,
                len(data),
                offset,
                len(piece),
                0,
                input_ack,
            )
            + piece
        )


def acknowledgement(data, session):
    if len(data) < ACK.size:
        raise IOError("Truncated calculator reply")
    (
        magic,
        version,
        status,
        credit,
        need_key,
        actual,
        accepted,
        displayed,
        limit,
        received,
        rejected,
    ) = ACK.unpack_from(data)
    if (
        magic != b"NDA1"
        or version != 1
        or actual != session
        or status > CLOSING
        or credit > 2
        or need_key > 1
        or limit not in (254, 1440)
    ):
        raise IOError("Invalid calculator reply")
    if status == CLOSING:
        raise ReceiverClosing("Calculator receiver is closing")
    events = []
    profile = None
    if len(data) > 32:
        if data[32:36] == b"NDS1":
            count = struct.unpack_from("<I", data, 36)[0] if len(data) >= 40 else 0
            if count not in (29, 48, 80) or len(data) != 40 + count * 4:
                raise IOError("Truncated receiver timings")
            words = struct.unpack_from("<" + str(count) + "I", data, 40)
            if words[0] & ~3:
                raise IOError("Invalid profiling flags")
            profile = dict(
                enabled=bool(words[0] & 1),
                high_resolution=bool(words[0] & 2),
                hz=words[1],
                os_hz=words[2],
                period=words[3],
                timer_control=words[4],
                frames=words[5],
                decoder_calls=words[6],
                received_during_active_frame=words[7],
                received_during_decoder_call=words[8],
                max_used_slots=words[9],
                foreground_task=words[10],
                callback_task=words[11],
                bad_clock_samples=words[12],
            )
            for i, name in enumerate(
                ["decode_convert", "blit", "usb_read", "usb_write", "irq_poll", "os_sleep", "input"]
            ):
                profile[name + "_ticks"] = words[13 + i * 2] | words[14 + i * 2] << 32
            profile["elapsed_ticks"] = words[27] | words[28] << 32
            if count >= 48:
                profile["timer_base"] = words[29]
                profile["timers"] = [
                    dict(
                        load=words[30 + i * 4],
                        value=words[31 + i * 4],
                        control=words[32 + i * 4],
                        raw_interrupt=words[33 + i * 4],
                    )
                    for i in range(4)
                ]
                profile["timer_sources"] = words[46:48]
            if count == 80:
                if words[48] != 1 or words[49] > 1 or words[78] != 5504:
                    raise IOError("Invalid SRAM experiment timings")
                profile["sram_tables"] = dict(
                    enabled=bool(words[49]),
                    phase=words[50],
                    sram_frames=words[51],
                    ram_frames=words[52],
                    busy_skips=words[53],
                    layout_skips=words[54],
                    stack_skips=words[55],
                    restore_errors=words[56],
                    hz=words[57],
                    last_reason=words[68],
                    timer_errors=words[69],
                    timer_base=words[70],
                    period=words[71],
                    usb_dma=words[72:74],
                    host_commands=words[74:76],
                    dma_busy_mask=words[76],
                    disabled_frames=words[77],
                    table_bytes=words[78],
                )
                for i, name in enumerate(
                    ["sram_convert", "ram_convert", "setup", "restore", "verify"]
                ):
                    profile["sram_tables"][name + "_ticks"] = (
                        words[58 + i * 2] | words[59 + i * 2] << 32
                    )
            return dict(
                status=status,
                credit=credit,
                need_key=need_key,
                accepted=accepted,
                displayed=displayed,
                limit=limit,
                received=received,
                rejected=rejected,
                events=[],
                reply_bytes=len(data),
                profile=profile,
            )
        if len(data) < 40 or data[32:36] != b"NDE1":
            raise IOError("Invalid input reply")
        count = struct.unpack_from("<I", data, 36)[0]
        if count > 17 or len(data) != 40 + count * 12:
            raise IOError("Truncated input events")
        for i in range(count):
            event = EVENT.unpack_from(data, 40 + i * 12)
            seq, kind, flags, code, x, y = event
            if (
                not seq
                or kind not in range(1, 6)
                or flags & ~3
                or (kind in (1, 4, 5) and (flags or code))
                or (kind == 2 and (code not in (1, 2, 3) or flags > 1 or x or y))
                or (kind == 3 and (code > 255 or not code or x or y))
                or (kind == 5 and (x or y))
            ):
                raise IOError("Invalid input event")
            if events and seq <= events[-1][0]:
                raise IOError("Unordered input events")
            events.append(event)
    return dict(
        status=status,
        credit=credit,
        need_key=need_key,
        accepted=accepted,
        displayed=displayed,
        limit=limit,
        received=received,
        rejected=rejected,
        events=events,
        reply_bytes=len(data),
    )
