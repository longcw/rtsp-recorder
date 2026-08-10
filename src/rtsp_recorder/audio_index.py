"""Per-stream audio-peaks index.

Persists each finalized recording's audio waveform to a sidecar JSON file
(`<stream_dir>/.audio.json`). Deliberately separate from `.idle.json`: the
file listing reads that index on every poll, and peak arrays are two orders
of magnitude larger than an idle flag.

File shape:
    {
        "YYYY-MM-DD_HH-MM-SS.mp4": {"peaks": "<base64 uint8>", "duration": 300.0},
        ...
    }

`peaks` is null when the recording carries no audio track. A missing key
means the file has not been looked at yet, which is what lets the analyzer
backfill recordings made before this index existed.
"""
from __future__ import annotations

import threading
from pathlib import Path

from . import json_sidecar

INDEX_FILENAME = ".audio.json"
_lock = threading.Lock()


def index_path(stream_dir: Path) -> Path:
    return stream_dir / INDEX_FILENAME


def load(stream_dir: Path) -> dict[str, dict]:
    return json_sidecar.load(index_path(stream_dir))


def save(stream_dir: Path, data: dict[str, dict]) -> None:
    json_sidecar.save(index_path(stream_dir), data)


def set_peaks(
    stream_dir: Path,
    filename: str,
    *,
    peaks: str | None,
    duration: float | None,
) -> None:
    """Record one file's waveform. `peaks=None` marks it as having no audio."""
    with _lock:
        data = load(stream_dir)
        entry: dict = {"peaks": peaks}
        if duration is not None:
            entry["duration"] = duration
        data[filename] = entry
        save(stream_dir, data)


def drop_missing(stream_dir: Path, present: set[str]) -> None:
    """Remove index entries for files that no longer exist on disk."""
    with _lock:
        data = load(stream_dir)
        stale = [k for k in data if k not in present]
        if not stale:
            return
        for k in stale:
            data.pop(k, None)
        save(stream_dir, data)
