"""Tests for fragmented -> faststart MP4 conversion.

`is_fragmented` is exercised with hand-built box headers (no ffmpeg needed).
The full `ensure_faststart` round-trip generates a real fragmented segment
with ffmpeg, so it's skipped when ffmpeg isn't on PATH.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import struct
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rtsp_recorder import faststart

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _box(box_type: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(payload)) + box_type + payload


class IsFragmentedTest(unittest.TestCase):
    def _write(self, name: str, data: bytes) -> Path:
        p = Path("/tmp") / name
        p.write_bytes(data)
        self.addCleanup(p.unlink)
        return p

    def test_detects_moof(self) -> None:
        # ftyp, empty moov, then a moof -> fragmented.
        data = _box(b"ftyp", b"isom") + _box(b"moov") + _box(b"moof")
        self.assertTrue(faststart.is_fragmented(self._write("_frag.mp4", data)))

    def test_plain_moov_mdat_is_not_fragmented(self) -> None:
        data = _box(b"ftyp", b"isom") + _box(b"moov", b"x" * 32) + _box(b"mdat", b"y" * 64)
        self.assertFalse(faststart.is_fragmented(self._write("_plain.mp4", data)))

    def test_trailing_size_zero_box_terminates(self) -> None:
        # size==0 means "runs to EOF"; the walk must stop, not loop.
        data = _box(b"ftyp", b"isom") + struct.pack(">I", 0) + b"mdat" + b"z" * 16
        self.assertFalse(faststart.is_fragmented(self._write("_eof.mp4", data)))

    def test_missing_file_is_not_fragmented(self) -> None:
        self.assertFalse(faststart.is_fragmented(Path("/tmp/_does_not_exist.mp4")))


@unittest.skipUnless(_HAS_FFMPEG, "ffmpeg not available")
class EnsureFaststartTest(unittest.IsolatedAsyncioTestCase):
    def _make_fragmented(self) -> Path:
        out = Path("/tmp/_ensure_frag.mp4")
        self.addCleanup(lambda: out.unlink(missing_ok=True))
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=1:size=128x96:rate=10",
                "-c:v", "mpeg4",
                "-movflags", "+empty_moov+default_base_moof+frag_keyframe",
                "-f", "mp4", str(out),
            ],
            check=True,
        )
        return out

    async def test_converts_and_is_idempotent(self) -> None:
        p = self._make_fragmented()
        self.assertTrue(faststart.is_fragmented(p))

        old = time.time() - 3600
        os.utime(p, (old, old))

        self.assertTrue(await faststart.ensure_faststart(p))
        self.assertFalse(faststart.is_fragmented(p))
        # mtime preserved so retention age is unaffected.
        self.assertAlmostEqual(p.stat().st_mtime, old, delta=1)
        # Already faststart -> no-op.
        self.assertFalse(await faststart.ensure_faststart(p))
        # No temp file left behind.
        self.assertFalse((p.parent / (p.name + ".faststart.tmp")).exists())


if __name__ == "__main__":
    unittest.main()
