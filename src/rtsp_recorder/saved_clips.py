"""Permanent clips exported from recordings, kept outside retention.

Each clip is a directory ``<root>/<id>/`` holding ``clip.json`` and its
renditions: ``1x.mp4`` is a stream copy of the source span (full quality,
with audio) and doubles as the master every faster rendition is encoded from,
so a clip can be re-exported at any speed after its recording is pruned.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from . import json_sidecar

logger = logging.getLogger(__name__)

# Renditions are written under this suffix and renamed into place when done,
# so a crash mid-encode never leaves a truncated file that looks finished.
TMP_SUFFIX = ".part"


def rendition_name(speed: float) -> str:
    return f"{speed:g}x.mp4"


def load(root: Path, clip_id: str) -> dict | None:
    meta = json_sidecar.load(root / clip_id / "clip.json")
    return meta if meta.get("id") == clip_id else None


def save(root: Path, meta: dict) -> None:
    json_sidecar.save(root / meta["id"] / "clip.json", meta)


def list_all(root: Path) -> list[dict]:
    """Every clip with a finished master, newest first."""
    clips = []
    for d in root.iterdir() if root.is_dir() else ():
        meta = load(root, d.name)
        if meta is not None and (d / rendition_name(1)).is_file():
            clips.append(meta)
    clips.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return clips


def delete(root: Path, clip_id: str) -> None:
    shutil.rmtree(root / clip_id, ignore_errors=True)


def sweep(root: Path) -> None:
    """Drop what an interrupted export left: partial renditions and masterless clips."""
    root.mkdir(parents=True, exist_ok=True)
    for d in root.iterdir():
        if not d.is_dir():
            continue
        for part in d.glob(f"*{TMP_SUFFIX}"):
            part.unlink(missing_ok=True)
        meta = load(root, d.name)
        if meta is None or not (d / rendition_name(1)).is_file():
            logger.info("saved clips: removing incomplete clip %s", d.name)
            delete(root, d.name)
            continue
        # renditions whose encode never finished
        have = [s for s in meta.get("speeds", []) if (d / rendition_name(s)).is_file()]
        if have != meta.get("speeds"):
            meta["speeds"] = have
            save(root, meta)
