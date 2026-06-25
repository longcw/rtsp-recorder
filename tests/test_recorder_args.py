"""The recorder must tag HEVC output as hvc1 (Apple-playable) and only HEVC."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rtsp_recorder.models import Stream
from rtsp_recorder.recorder import StreamRecorder


def _make_recorder() -> StreamRecorder:
    stream = Stream(name="cam", url="rtsp://example/stream", enabled=True)
    return StreamRecorder(stream, Path("/tmp"), segment_seconds=60, tz="UTC")


def _has_hvc1_tag(args: list[str]) -> bool:
    return any(
        args[i] == "-tag:v" and args[i + 1] == "hvc1"
        for i in range(len(args) - 1)
    )


class FfmpegArgsTaggingTest(unittest.TestCase):
    def test_hevc_gets_hvc1_tag(self) -> None:
        rec = _make_recorder()
        rec._video_codec = "hevc"
        self.assertTrue(_has_hvc1_tag(rec._ffmpeg_args("out_%03d.mp4")))

    def test_h264_is_not_tagged(self) -> None:
        rec = _make_recorder()
        rec._video_codec = "h264"
        self.assertFalse(_has_hvc1_tag(rec._ffmpeg_args("out_%03d.mp4")))

    def test_unknown_codec_is_not_tagged(self) -> None:
        # Probe failed / not run yet — record untagged; the faststart loop
        # re-tags finalized segments as a fallback.
        rec = _make_recorder()
        self.assertIsNone(rec._video_codec)
        self.assertFalse(_has_hvc1_tag(rec._ffmpeg_args("out_%03d.mp4")))


if __name__ == "__main__":
    unittest.main()
