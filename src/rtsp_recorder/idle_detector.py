"""Idle-recording detector.

Uses ffmpeg to decode a finalized segment file at 1 fps, scaled down to a
small grayscale thumbnail, and compares consecutive frames. A recording is
classified as idle ONLY when the frames are very stable across the whole
clip — the feature is tuned for high precision (don't call moving footage
idle), accepting that some genuinely-idle clips will be missed.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Decoder output geometry. 64x36 is enough to register meaningful motion
# (a moving person in a typical FOV covers many pixels at this resolution)
# while keeping decode + diff cheap.
_W, _H = 64, 36
_FRAME_BYTES = _W * _H
_FPS = "1"

# Idle requires BOTH:
#   * the largest consecutive-frame mean-abs-diff is below this
#   * the spread of per-frame means stays within this small range
# Tuned conservatively — sensor noise on a static scene is typically
# 0.5–2.0; anything noticeably moving lands well above 2.5.
_MAX_CONSEC_DIFF = 2.5
_MEAN_RANGE = 4.0

# Need at least this many decoded frames to make a call. Short clips or
# corrupted decodes return None (unknown) rather than risking a false idle.
_MIN_FRAMES = 4


async def detect_idle(path: Path) -> bool | None:
    """Return True if the recording looks fully idle, False if motion was
    seen, or None if we couldn't analyze it (no frames, decode error, etc.).
    """
    frames = await _decode_thumbnail_frames(path)
    if frames is None or frames.shape[0] < _MIN_FRAMES:
        return None
    return _classify(frames)


def _classify(frames: np.ndarray) -> bool:
    # frames: (N, H*W) uint8
    f = frames.astype(np.int16)
    consec_diff = np.mean(np.abs(np.diff(f, axis=0)), axis=1)
    per_frame_mean = np.mean(f, axis=1)
    return bool(
        consec_diff.max() < _MAX_CONSEC_DIFF
        and (per_frame_mean.max() - per_frame_mean.min()) < _MEAN_RANGE
    )


async def _decode_thumbnail_frames(path: Path) -> np.ndarray | None:
    """Run ffmpeg, return an (N, H*W) uint8 array of decoded thumbnails."""
    args = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-i", str(path),
        "-vf", f"fps={_FPS},scale={_W}:{_H}",
        "-pix_fmt", "gray",
        "-f", "rawvideo",
        "-",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning("idle-detector: ffmpeg not on PATH")
        return None

    out, err = await proc.communicate()
    if proc.returncode != 0:
        logger.debug(
            "idle-detector: ffmpeg failed for %s: %s",
            path.name,
            err.decode(errors="replace").strip()[:200],
        )
        return None
    if not out:
        return None
    n = len(out) // _FRAME_BYTES
    if n == 0:
        return None
    usable = n * _FRAME_BYTES
    return np.frombuffer(out[:usable], dtype=np.uint8).reshape(n, _FRAME_BYTES)
