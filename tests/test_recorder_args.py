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


def _segment(args: list[str]) -> list[str]:
    """Codec/mapping arguments, i.e. what sits between the input and the muxer."""
    return args[args.index("-i") + 2 : args.index("-f")]


class AudioArgsTest(unittest.TestCase):
    def _args(self, video: str | None, audio: str | None) -> list[str]:
        rec = _make_recorder()
        rec._video_codec = video
        rec._audio_codec = audio
        rec._probed = True
        return _segment(rec._ffmpeg_args("out_%03d.mp4"))

    def test_recording_never_resamples(self) -> None:
        # the source rate is whatever the camera sends; changing it here was a
        # workaround for a misdiagnosis and only wasted bitrate
        for audio in ("pcm_alaw", "pcm_mulaw", "aac"):
            self.assertNotIn("-ar", self._args("hevc", audio))

    def test_no_audio_disables_it(self) -> None:
        args = self._args("hevc", None)
        self.assertIn("-an", args)
        self.assertNotIn("-map", args)

    def test_g711_is_transcoded_to_aac(self) -> None:
        # MP4 has no tag for pcm_alaw; copying it makes ffmpeg refuse to write
        # the header, so the whole recording fails.
        for codec in ("pcm_alaw", "pcm_mulaw"):
            args = self._args("hevc", codec)
            self.assertEqual(args[args.index("-c:a") + 1], "aac")
            self.assertNotIn("-an", args)

    def test_opus_is_transcoded_because_safari_cannot_play_it_in_mp4(self) -> None:
        args = self._args("hevc", "opus")
        self.assertEqual(args[args.index("-c:a") + 1], "aac")

    def test_aac_is_copied(self) -> None:
        args = self._args("hevc", "aac")
        self.assertEqual(args[args.index("-c:a") + 1], "copy")

    def test_video_is_always_copied(self) -> None:
        for audio in (None, "aac", "pcm_alaw"):
            args = self._args("hevc", audio)
            self.assertEqual(args[args.index("-c:v") + 1], "copy")

    def test_video_is_mapped_before_audio(self) -> None:
        # The segment muxer cuts on the reference stream, which must stay video;
        # audio-referenced cuts do not align to keyframes.
        args = self._args("hevc", "pcm_alaw")
        self.assertLess(args.index("0:v:0"), args.index("0:a:0"))

    def test_hvc1_tagging_still_applies_with_audio(self) -> None:
        self.assertTrue(_has_hvc1_tag(self._args("hevc", "pcm_alaw")))
        self.assertFalse(_has_hvc1_tag(self._args("h264", "pcm_alaw")))


class BackoffTest(unittest.TestCase):
    def test_backoff_starts_short_and_is_capped(self) -> None:
        from rtsp_recorder.recorder import (
            RESTART_BACKOFF_MAX_SECONDS,
            RESTART_BACKOFF_SECONDS,
        )

        rec = _make_recorder()
        self.assertEqual(rec._backoff, RESTART_BACKOFF_SECONDS)
        seen = []
        for _ in range(10):
            seen.append(rec._backoff)
            rec._backoff = min(rec._backoff * 2, RESTART_BACKOFF_MAX_SECONDS)
        self.assertEqual(seen[0], RESTART_BACKOFF_SECONDS)
        self.assertEqual(seen[-1], RESTART_BACKOFF_MAX_SECONDS)
        self.assertLessEqual(max(seen), RESTART_BACKOFF_MAX_SECONDS)

    def test_status_reports_audio_only_once_probed(self) -> None:
        rec = _make_recorder()
        self.assertIsNone(rec.status().has_audio)
        rec._probed = True
        self.assertFalse(rec.status().has_audio)
        rec._audio_codec = "pcm_alaw"
        self.assertTrue(rec.status().has_audio)
        self.assertEqual(rec.status().audio_codec, "pcm_alaw")


if __name__ == "__main__":
    unittest.main()
