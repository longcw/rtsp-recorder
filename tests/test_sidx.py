"""Tests for the indexed view of a live fragmented segment.

Generates a real fragmented segment (video + audio) with ffmpeg, cut short
mid-fragment the way a live file is, and checks ffprobe reads the view's
duration from the header instead of walking every fragment.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rtsp_recorder import sidx


def _materialize(src: Path, pieces: list, out: Path) -> None:
    with open(src, "rb") as f, open(out, "wb") as o:
        for p in pieces:
            if isinstance(p, bytes):
                o.write(p)
            else:
                f.seek(p[0])
                o.write(f.read(p[1]))


def _probe(path: Path) -> tuple[float, int]:
    """Return ``(duration, bytes read)`` for one ffprobe open of ``path``."""
    out = subprocess.run(
        ["ffprobe", "-v", "debug", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    read = next(
        int(line.split("Statistics: ")[1].split(" bytes")[0])
        for line in out.stderr.splitlines()
        if "Statistics:" in line
    )
    return float(json.loads(out.stdout)["format"]["duration"]), read


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not available")
class LiveViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.src = self.dir / "live.mp4"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=20:size=640x480:rate=10",
                "-f", "lavfi", "-i", "sine=duration=20",
                "-c:v", "mpeg4", "-q:v", "2", "-g", "10", "-c:a", "aac",
                "-movflags", "+empty_moov+default_base_moof+frag_keyframe",
                "-f", "mp4", str(self.src),
            ],
            check=True,
        )

    def test_indexed_view_opens_from_header(self) -> None:
        # cut mid-way through a fragment, like a segment ffmpeg is still writing
        limit = self.src.stat().st_size * 3 // 4
        index = sidx.read_index(self.src, limit)
        pieces, total = index.view()
        self.assertAlmostEqual(index.seconds, _probe(self.src)[0] * 3 / 4, delta=2)
        view = self.dir / "view.mp4"
        _materialize(self.src, pieces, view)
        self.assertEqual(view.stat().st_size, total)

        duration, read = _probe(view)
        self.assertGreater(duration, 12)
        self.assertLess(duration, 16)
        self.assertLess(read, total // 4)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-i", str(view), "-f", "null", "-"],
            check=True,
        )

    def test_finalized_file_is_left_alone(self) -> None:
        plain = self.dir / "plain.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(self.src), "-c", "copy",
             "-movflags", "+faststart", str(plain)],
            check=True,
        )
        self.assertIsNone(sidx.read_index(plain, plain.stat().st_size))


if __name__ == "__main__":
    unittest.main()
