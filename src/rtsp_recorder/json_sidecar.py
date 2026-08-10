"""Atomic JSON-dict sidecar files.

The idle index and the audio-peaks index are both per-stream-directory JSON
dicts keyed by recording filename, so the crash-safe write (tempfile, fsync,
rename) lives here once instead of in each of them.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def load(path: Path) -> dict[str, dict]:
    """Read a sidecar, treating any damage as an empty index."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    except OSError as e:
        logger.warning("sidecar: read %s failed: %s", path, e)
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("sidecar: invalid json at %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save(path: Path, data: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f"{path.name}-", suffix=".tmp", dir=str(path.parent))
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
