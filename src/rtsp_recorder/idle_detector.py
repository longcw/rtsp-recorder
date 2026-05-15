"""Idle-recording detector.

Uses ffmpeg to decode a finalized segment file at 1 fps, scaled down to a
small grayscale thumbnail, and compares consecutive frames. A recording is
classified as idle ONLY when the frames are very stable across the whole
clip — the feature is tuned for high precision (don't call moving footage
idle), accepting that some genuinely-idle clips will be missed.

Also probes the file with ffprobe to capture the real video duration —
the file mtime minus the filename strftime is a noisy approximation
because segment cuts only happen on keyframes, so actual content can run
seconds past the nominal boundary.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Decoder output geometry. 64x36 is enough to register meaningful motion
# (a moving person in a typical FOV covers many pixels at this resolution)
# while keeping decode + diff cheap.
_W, _H = 64, 36
_FRAME_BYTES = _W * _H
# Sample one frame every 2.5s. Higher rates (1 fps) caught marginally more
# brief motion but ate measurable ffmpeg decode time; at 2.5s a 5-minute
# clip yields ~120 frames — plenty for the percentile-based classifier to
# distinguish real motion from sensor noise, while running ~2.5x cheaper.
_FPS = "1/2.5"

# Idle requires BOTH:
#   * the 95th-percentile consecutive-frame mean-abs-diff stays below
#     `motion_threshold` (passed in from config)
#   * the spread of per-frame means stays below `2 * motion_threshold`
#
# We use a high percentile rather than max because single-frame spikes
# (H.264 keyframe boundaries, brief autoexposure ticks, codec artifacts)
# happen even on otherwise-still scenes and were kicking obviously-idle
# clips into the "action" bucket.
_CONSEC_DIFF_PERCENTILE = 95
DEFAULT_MOTION_THRESHOLD = 5.0

# Need at least this many decoded frames to make a call. Short clips or
# corrupted decodes return None (unknown) rather than risking a false idle.
_MIN_FRAMES = 4


@dataclass
class AnalysisResult:
    idle: bool | None
    duration_seconds: float | None


async def analyze(
    path: Path,
    *,
    motion_threshold: float = DEFAULT_MOTION_THRESHOLD,
) -> AnalysisResult:
    """Probe `path` for video duration and idle classification.

    Either field may be None independently: a file with a readable duration
    but too few decoded frames returns `idle=None, duration=...`, and so on.
    """
    frames_task = asyncio.create_task(_decode_thumbnail_frames(path))
    duration_task = asyncio.create_task(_probe_duration(path))
    frames, duration = await asyncio.gather(frames_task, duration_task)
    if frames is None or frames.shape[0] < _MIN_FRAMES:
        idle = None
    else:
        idle = _classify(frames, motion_threshold)
    return AnalysisResult(idle=idle, duration_seconds=duration)


def _classify(frames: np.ndarray, motion_threshold: float) -> bool:
    # frames: (N, H*W) uint8
    f = frames.astype(np.int16)
    consec_diff = np.mean(np.abs(np.diff(f, axis=0)), axis=1)
    per_frame_mean = np.mean(f, axis=1)
    diff_stat = float(np.percentile(consec_diff, _CONSEC_DIFF_PERCENTILE))
    return bool(
        diff_stat < motion_threshold
        and (per_frame_mean.max() - per_frame_mean.min()) < (2.0 * motion_threshold)
    )


async def _probe_duration(path: Path) -> float | None:
    """Return the container's reported duration in seconds, or None."""
    args = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    text = out.decode(errors="replace").strip()
    if not text or text.upper() == "N/A":
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        return None


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
