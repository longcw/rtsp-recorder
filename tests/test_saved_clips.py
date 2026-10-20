"""Tests for the saved-clip store's listing and crash cleanup."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rtsp_recorder import saved_clips


class SavedClipsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)

    def _clip(self, clip_id: str, speeds: list[float], files: list[str], created: str = "") -> None:
        saved_clips.save(self.root, {"id": clip_id, "created_at": created, "speeds": speeds})
        for name in files:
            (self.root / clip_id / name).write_bytes(b"x")

    def test_sweep_drops_partial_work(self) -> None:
        self._clip("done", [1.0, 4.0], ["1x.mp4", "4x.mp4", "8x.mp4.part"])
        # master never finished
        self._clip("nomaster", [], ["1x.mp4.part"])
        # metadata claims a rendition whose file is gone
        self._clip("stale", [1.0, 2.0], ["1x.mp4"])

        saved_clips.sweep(self.root)

        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["done", "stale"])
        self.assertFalse((self.root / "done" / "8x.mp4.part").exists())
        self.assertEqual(saved_clips.load(self.root, "stale")["speeds"], [1.0])

    def test_list_is_newest_first_and_skips_masterless(self) -> None:
        self._clip("old", [1.0], ["1x.mp4"], created="2026-01-01T00:00:00+00:00")
        self._clip("new", [1.0], ["1x.mp4"], created="2026-02-01T00:00:00+00:00")
        self._clip("running", [], [], created="2026-03-01T00:00:00+00:00")
        self.assertEqual([m["id"] for m in saved_clips.list_all(self.root)], ["new", "old"])


if __name__ == "__main__":
    unittest.main()
