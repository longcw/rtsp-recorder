"""Per-stream idle index.

Persists each finalized recording's analyzed-idle status to a sidecar JSON
file (`<stream_dir>/.idle.json`). Kept separate from the main config so it
can be updated freely from the analyzer loop without thrashing the config
file's locks.

File shape:
    {
        "YYYY-MM-DD_HH-MM-SS.mp4": {"idle": true},
        ...
    }

Missing key → not yet analyzed. The pruner removes entries whose files are
gone; the analyzer never re-runs once an entry exists.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

INDEX_FILENAME = ".idle.json"
_lock = threading.Lock()


def index_path(stream_dir: Path) -> Path:
    return stream_dir / INDEX_FILENAME


def load(stream_dir: Path) -> dict[str, dict]:
    path = index_path(stream_dir)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    except OSError as e:
        logger.warning("idle-index: read %s failed: %s", path, e)
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("idle-index: invalid json at %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save(stream_dir: Path, data: dict[str, dict]) -> None:
    path = index_path(stream_dir)
    stream_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".idle-", suffix=".json", dir=str(stream_dir))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def get_idle(stream_dir: Path, filename: str) -> bool | None:
    entry = load(stream_dir).get(filename)
    if not isinstance(entry, dict):
        return None
    v = entry.get("idle")
    return v if isinstance(v, bool) else None


def set_idle(stream_dir: Path, filename: str, idle: bool) -> None:
    """Manual override path — only flips the idle flag, keeps any cached
    duration alongside.
    """
    with _lock:
        data = load(stream_dir)
        entry = data.get(filename)
        merged: dict = entry.copy() if isinstance(entry, dict) else {}
        merged["idle"] = idle
        data[filename] = merged
        save(stream_dir, data)


def set_analysis(
    stream_dir: Path,
    filename: str,
    *,
    idle: bool | None,
    duration_seconds: float | None,
) -> None:
    """Analyzer write path: stores the full analysis result for one file.

    Only included fields are written — passing None for either skips it.
    A file may legitimately have a duration but no idle (decode too short
    to classify), or an idle but no duration (ffprobe missing).
    """
    with _lock:
        data = load(stream_dir)
        entry: dict = {}
        if idle is not None:
            entry["idle"] = idle
        if duration_seconds is not None:
            entry["duration"] = duration_seconds
        if not entry:
            return
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
