"""Connection failures persist without logging every frame or input event."""

from pathlib import Path
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from diagnostics import ConnectionLog

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "logs/connection.log"
    log = ConnectionLog(path, maximum=1000)
    log.status("30.0 FPS displayed / 100 kbps")
    assert not path.exists()
    for frame in range(100):
        log.frame(
            frame, 1234, False, {"status": 0, "displayed": frame - 1, "events": ["private input"]}
        )
    assert len(log.frames) == 16 and not path.exists()
    try:
        raise TimeoutError("USB transfer timed out")
    except TimeoutError as error:
        log.failure(error, 42, {"status": 3, "need_key": 1, "events": ["private input"]})
    entry = json.loads(path.read_text())
    assert entry["error_type"] == "TimeoutError" and entry["session"] == 42
    assert entry["receiver"] == {"status": 3, "need_key": 1}
    assert "private input" not in path.read_text()
    assert entry["recent_frames"][0]["frame"] == 84
    for index in range(12):
        log.record("test", message="x" * 900)
    assert path.exists() and path.with_name("connection.log.3").exists()
    assert len(list(path.parent.glob("connection.log*"))) == 4
    blocked = Path(directory) / "not-a-directory"
    blocked.write_text("fixture")
    log = ConnectionLog(blocked / "connection.log")
    log.status("Connecting")
    assert log.path is None and "Could not save" in log.notice
    capture = ConnectionLog(
        Path(directory) / "capture/connection.log", capture_stream=True, stream_limit=8
    )
    capture.frame(1, 2, False, {}, b"aa")
    assert not capture.video
    capture.frame(2, 2, True, {}, b"bb")
    capture.frame(3, 2, False, {}, b"cc")
    capture.encoded(3, False, b"cc")  # Same frame's ACK must not duplicate video.
    capture.encoded(4, False, b"dd")  # No reply arrives for the submitted frame.
    capture.failure(ValueError("Fixture reset"), 73, {})
    dumps = list((capture.path.parent / "failure-streams").glob("*.h264"))
    assert len(dumps) == 1 and dumps[0].read_bytes() == b"bbccdd"
    meta = json.loads(dumps[0].with_suffix(".json").read_text())
    assert [f["frame"] for f in meta["frames"]] == [2, 3, 4]
    capture.failure(ValueError("Fixture teardown"), 0, {})
    assert len(list((capture.path.parent / "failure-streams").glob("*.h264"))) == 1
    for _ in range(4):
        capture.failure(ValueError("Fixture reset"), 73, {})
    assert len(list((capture.path.parent / "failure-streams").glob("*.h264"))) == 3
    capture.frame(5, 6, False, {}, b"dddddd")
    assert not capture.video and capture.video_bytes == 0
print(
    "PASS bounded frame ring, persistent failure context, log rotation, input exclusion and explicit write failure"
)
