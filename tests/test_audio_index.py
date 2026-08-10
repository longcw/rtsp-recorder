import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rtsp_recorder import audio_index  # noqa: E402


class AudioIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="audio-index-test-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_missing_index_reads_as_empty(self) -> None:
        self.assertEqual(audio_index.load(self.dir), {})

    def test_round_trip(self) -> None:
        audio_index.set_peaks(self.dir, "a.mp4", peaks="AAEC", duration=300.5)
        entry = audio_index.load(self.dir)["a.mp4"]
        self.assertEqual(entry["peaks"], "AAEC")
        self.assertEqual(entry["duration"], 300.5)

    def test_no_audio_is_recorded_as_null_not_absent(self) -> None:
        # An absent key means "not analyzed yet" and would be re-analyzed
        # forever, so a file without audio has to store an explicit null.
        audio_index.set_peaks(self.dir, "a.mp4", peaks=None, duration=60.0)
        data = audio_index.load(self.dir)
        self.assertIn("a.mp4", data)
        self.assertIsNone(data["a.mp4"]["peaks"])

    def test_damaged_index_reads_as_empty(self) -> None:
        audio_index.index_path(self.dir).write_text("{ not json")
        self.assertEqual(audio_index.load(self.dir), {})

    def test_drop_missing_keeps_present_files(self) -> None:
        audio_index.set_peaks(self.dir, "a.mp4", peaks="AA", duration=1.0)
        audio_index.set_peaks(self.dir, "b.mp4", peaks="AA", duration=1.0)
        audio_index.drop_missing(self.dir, {"a.mp4"})
        self.assertEqual(list(audio_index.load(self.dir)), ["a.mp4"])


if __name__ == "__main__":
    unittest.main()
