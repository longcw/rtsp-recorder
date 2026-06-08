"""Regression test for the network-hang wedge.

When the network drops, ffmpeg can get stuck in a blocking socket read and
ignore SIGTERM (its handler only sets a flag the main loop polls between
operations, which it never reaches while blocked in recv()). The watchdog's
kill path must therefore escalate to SIGKILL so the process actually dies and
the recorder's restart loop can proceed. Before the fix the kill was
SIGTERM-only and a SIGTERM-ignoring child survived forever -> "never restarts".
"""
from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rtsp_recorder.models import Stream
from rtsp_recorder.recorder import StreamRecorder

# A child that ignores SIGTERM and would otherwise sleep far past the test,
# standing in for a network-hung ffmpeg blocked on a dead RTSP socket.
_SIGTERM_IGNORING_CHILD = (
    "import signal, time; "
    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "time.sleep(300)"
)


def _make_recorder(tmp: Path) -> StreamRecorder:
    stream = Stream(name="cam", url="rtsp://example/stream", enabled=True)
    return StreamRecorder(stream, tmp, segment_seconds=60, tz="UTC")


class KillProcTest(unittest.IsolatedAsyncioTestCase):
    async def test_escalates_to_sigkill_when_sigterm_ignored(self) -> None:
        rec = _make_recorder(Path("/tmp"))
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", _SIGTERM_IGNORING_CHILD
        )
        rec._proc = proc

        start = time.monotonic()
        # Short grace so the test is quick; the real value is larger.
        await rec._kill_proc(grace=1.0)
        elapsed = time.monotonic() - start

        # The process must actually be dead, and we must not have blocked
        # anywhere near the child's 300s sleep.
        self.assertIsNotNone(proc.returncode, "process should have exited")
        self.assertLess(elapsed, 10.0, "kill should not block on the hung child")

    async def test_returns_promptly_for_well_behaved_child(self) -> None:
        rec = _make_recorder(Path("/tmp"))
        # Exits immediately on SIGTERM (default disposition).
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(300)"
        )
        rec._proc = proc

        start = time.monotonic()
        await rec._kill_proc(grace=5.0)
        elapsed = time.monotonic() - start

        self.assertIsNotNone(proc.returncode)
        # Should return on the SIGTERM path, well before the grace window.
        self.assertLess(elapsed, 4.0)


if __name__ == "__main__":
    unittest.main()
