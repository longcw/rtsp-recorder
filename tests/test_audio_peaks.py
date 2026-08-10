import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rtsp_recorder.audio_peaks import (  # noqa: E402
    analyze_audio,
    downsample,
    encode,
    peaks_from_samples,
)

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


class PeaksMathTest(unittest.TestCase):
    def test_silence_is_all_zero(self) -> None:
        samples = np.zeros(8000 * 30, dtype=np.int16)
        self.assertEqual(set(peaks_from_samples(samples, 30.0)), {0})

    def test_full_scale_reads_max(self) -> None:
        samples = np.zeros(8000 * 30, dtype=np.int16)
        samples[:] = 32767
        self.assertEqual(set(peaks_from_samples(samples, 30.0)), {255})

    def test_burst_stands_out_from_silence(self) -> None:
        # Two seconds of full-scale audio in the middle of a silent minute.
        samples = np.zeros(8000 * 60, dtype=np.int16)
        samples[8000 * 30 : 8000 * 32] = 32767
        peaks = peaks_from_samples(samples, 60.0)
        loud = [i for i, v in enumerate(peaks) if v > 0]
        # 120 buckets over 60 s => half-second each, so the burst covers four.
        self.assertEqual(len(peaks), 120)
        self.assertEqual(loud, [60, 61, 62, 63])

    def test_bucket_count_is_clamped(self) -> None:
        short = np.zeros(8000 * 5, dtype=np.int16)
        self.assertEqual(len(peaks_from_samples(short, 5.0)), 60)
        long = np.zeros(8000 * 3600, dtype=np.int16)
        self.assertEqual(len(peaks_from_samples(long, 3600.0)), 600)

    def test_fewer_samples_than_buckets(self) -> None:
        # A near-empty audio track must not blow up the bucket math.
        self.assertEqual(len(peaks_from_samples(np.zeros(3, dtype=np.int16), 60.0)), 3)
        self.assertEqual(peaks_from_samples(np.zeros(0, dtype=np.int16), 60.0), [])

    def test_downsample_keeps_the_loudest_bucket(self) -> None:
        peaks = [0] * 600
        peaks[301] = 200
        smaller = downsample(encode(peaks), 48)
        self.assertIsNotNone(smaller)
        out = peaks_bytes(smaller)
        self.assertEqual(len(out), 48)
        self.assertEqual(max(out), 200)

    def test_downsample_leaves_short_arrays_alone(self) -> None:
        encoded = encode([1, 2, 3])
        self.assertEqual(downsample(encoded, 48), encoded)

    def test_downsample_rejects_garbage(self) -> None:
        self.assertIsNone(downsample("not base64!!", 48))


def peaks_bytes(encoded: str) -> bytes:
    import base64

    return base64.b64decode(encoded)


@unittest.skipUnless(_HAS_FFMPEG, "ffmpeg not available")
class AnalyzeAudioTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="peaks-test-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _build(self, name: str, *args: str) -> Path:
        path = self.dir / name
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, str(path)],
            check=True,
        )
        return path

    async def test_burst_is_visible_against_silence(self) -> None:
        # Thirty silent seconds with a tone from 10 s to 13 s.
        path = self._build(
            "burst.mp4",
            "-f", "lavfi", "-i", "testsrc=d=30:s=160x90:r=5",
            "-f", "lavfi", "-i", "sine=f=440:d=30",
            "-af", "volume=0:enable='not(between(t,10,13))'",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-ar", "8000", "-t", "30",
        )
        result = await analyze_audio(path)
        self.assertIsNotNone(result.peaks)
        peaks = result.peaks
        self.assertEqual(len(peaks), 60)  # 30 s at two buckets per second
        # Buckets 20-25 span 10-13 s. Allow a bucket of slack either side for
        # the encoder's ramp, but the silence well away from it must be flat.
        self.assertGreater(max(peaks[21:25]), 100)
        self.assertEqual(max(peaks[:18]), 0)
        self.assertEqual(max(peaks[28:]), 0)

    async def test_file_without_audio_reports_no_peaks(self) -> None:
        path = self._build(
            "silent.mp4",
            "-f", "lavfi", "-i", "testsrc=d=5:s=160x90:r=5",
            "-c:v", "libx264", "-preset", "ultrafast", "-an", "-t", "5",
        )
        result = await analyze_audio(path)
        self.assertIsNone(result.peaks)
        self.assertAlmostEqual(result.duration_seconds, 5.0, places=1)


if __name__ == "__main__":
    unittest.main()
