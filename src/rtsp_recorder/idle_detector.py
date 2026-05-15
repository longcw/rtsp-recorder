"""Idle-recording detector.

Decodes only the keyframes of a finalized segment file and looks for
*structural* motion between them. A recording is classified as idle ONLY
when frames are structurally stable across the whole clip — tuned for
catching small baby movements (a hand or leg in a crib) while ignoring
camera-level noise like sensor noise, slow lighting drift, and uniform
color/exposure shifts that change the whole frame at once.

Why we don't use mean-absolute-diff (the previous approach):
  A baby moving a hand changes ~100–500 pixels by 10–30 intensity units;
  shot noise and slow lighting drift change *every* pixel by 1–3 units.
  Averaged over the whole frame the two look the same, so the global
  mean treated them as comparable signals. We instead:

  1. Spatially blur each frame (boxblur in ffmpeg) so per-pixel sensor
     noise gets averaged out before any diff is taken.
  2. For each consecutive pair, compute the abs diff and subtract the
     per-pair *median* of that diff. A uniform brightness shift adds the
     same value to every pixel of the diff → the median absorbs it → it
     vanishes. A localized motion shows up as a cluster of outliers far
     above the median → it survives.
  3. Threshold each residual diff pixel at a fixed per-pixel level and
     compute the *fraction of the frame* that moved.

We only decode keyframes (`-skip_frame nokey`). Full decode of every P/B
frame just to drop most via an `fps` filter is wasted work; sampling at
the GOP cadence (~1–4 s for typical IP cameras) yields plenty of pairs
for percentile-based stats and runs roughly 5–10× cheaper.

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
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

# Decoder output geometry. 160x90 is fine enough that a baby's hand
# (~2–3% of typical FOV) covers a few-hundred-pixel cluster — large
# enough for the moving-fraction stat to register clearly without being
# drowned in per-pixel noise. Still tiny in absolute terms (~14 KiB per
# frame) so memory and numpy work stay negligible.
_W, _H = 160, 90
_FRAME_BYTES = _W * _H

# After subtracting the per-pair median diff, a "moving" pixel must
# exceed this on the 0–255 grayscale. Spatial boxblur cuts residual
# per-pixel noise to ~1–3 levels; a real edge crossing a pixel shifts it
# by 10+. 8 sits comfortably between the two.
_PIXEL_THRESHOLD = 8

# Per-pair motion is "percent of frame area showing real motion". The
# clip-level statistic is the 97th-percentile across all pairs — high
# enough that one-off codec / autoexposure spikes don't dominate, low
# enough that a *brief* motion event spanning only a few keyframes in a
# long clip still surfaces. For typical baby footage that's the hard
# case: the baby wiggles for a couple of seconds and then is still
# again; we want that to disqualify idle.
_MOTION_FRAC_PERCENTILE = 97

# Default motion_threshold (in percent-of-frame). Empirically the noise
# floor after blur + median-subtract sits well under 0.2% even on long
# clips, so a value of 1% leaves a comfortable 5× headroom while still
# triggering on a baby's hand moving (~0.3–1% of frame at 160×90). Tune
# up if you see false positives; tune down to catch finger-only wiggles.
DEFAULT_MOTION_THRESHOLD = 1.0

# Need at least this many decoded frames to make a call. Short clips or
# corrupted decodes return None (unknown) rather than risking a false
# idle.
_MIN_FRAMES = 4


@dataclass
class AnalysisResult:
    idle: bool | None
    duration_seconds: float | None


async def analyze(
    path: Path,
    *,
    motion_threshold: float = DEFAULT_MOTION_THRESHOLD,
    on_progress: Callable[[float], None] | None = None,
) -> AnalysisResult:
    """Probe `path` for video duration and idle classification.

    `on_progress`, if given, is called with a fraction in [0,1] as the
    decoder makes progress through the file. Callback is best-effort:
    cheap, fire-and-forget, may be called multiple times per second.

    Either result field may be None independently: a file with a
    readable duration but too few decoded frames returns `idle=None,
    duration=...`, and so on.
    """
    # Probe duration first so the decode-progress callback has a
    # denominator. Probe is ~50 ms — well below decode time — and the
    # tiny loss in parallelism is worth getting accurate progress.
    duration = await _probe_duration(path)

    def decoder_progress(out_time_seconds: float) -> None:
        if on_progress is None or duration is None or duration <= 0:
            return
        try:
            on_progress(min(1.0, out_time_seconds / duration))
        except Exception:  # pragma: no cover - callback is best-effort
            logger.debug("idle-detector: on_progress raised", exc_info=True)

    frames = await _decode_keyframes(path, on_progress=decoder_progress)
    if frames is None or frames.shape[0] < _MIN_FRAMES:
        idle = None
    else:
        idle = _classify(frames, motion_threshold)
    if on_progress is not None:
        try:
            on_progress(1.0)
        except Exception:  # pragma: no cover
            pass
    return AnalysisResult(idle=idle, duration_seconds=duration)


def _classify(frames: np.ndarray, motion_threshold: float) -> bool:
    """Return True when the clip looks structurally still.

    `frames` is (N, H*W) uint8. We work in int16 so diffs can be signed
    without overflow, take abs once, then run all reductions vectorized.
    """
    f = frames.astype(np.int16)
    # Per-pair abs diff: (N-1, P)
    diffs = np.abs(np.diff(f, axis=0))
    # Median-subtract along the pixel axis. The median is a robust
    # estimate of "what every pixel changed by" — for a uniform global
    # shift it equals the shift, leaving structural outliers alone.
    med = np.median(diffs, axis=1, keepdims=True)
    residual = diffs - med
    moving = residual > _PIXEL_THRESHOLD
    # Percent of frame moving, per pair.
    frac_pct = 100.0 * moving.mean(axis=1)
    stat = float(np.percentile(frac_pct, _MOTION_FRAC_PERCENTILE))
    return stat < motion_threshold


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


# Keys ffmpeg's `-progress` emits as `key=value` lines on its progress
# sink. We send progress to stderr (alongside any real error output) and
# use this set to tell the two kinds of line apart — only lines whose
# key is in here get silently consumed; anything else is treated as an
# error message worth logging.
_PROGRESS_KEYS = frozenset({
    "frame", "fps", "stream_0_0_q", "bitrate", "total_size",
    "out_time_us", "out_time_ms", "out_time", "dup_frames",
    "drop_frames", "speed", "progress",
})


async def _decode_keyframes(
    path: Path,
    *,
    on_progress: Callable[[float], None] | None = None,
) -> np.ndarray | None:
    """Decode key frames only, return an (N, H*W) uint8 array.

    `-skip_frame nokey` makes libavcodec discard non-key frames before
    motion compensation, so we pay decode cost only for I-frames — the
    big win over the previous fps-filter approach. The boxblur filter
    runs in C on the already-tiny scaled frame to suppress per-pixel
    sensor noise before we ever touch the bytes in numpy.

    `on_progress` is called with the most recently observed
    `out_time_us` (in seconds) from ffmpeg's progress sink. Callers
    convert that to a fraction-of-file using the probed duration.
    """
    args = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-skip_frame", "nokey",
        "-an",
        "-i", str(path),
        "-fps_mode", "passthrough",
        "-vf", f"scale={_W}:{_H},format=gray,boxblur=1:1",
        # Emit progress key=value lines onto stderr alongside the
        # (rare) real error output. We separate them in the reader by
        # checking the key against _PROGRESS_KEYS.
        "-progress", "pipe:2",
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

    assert proc.stdout is not None and proc.stderr is not None

    async def drain_stderr() -> str:
        """Read stderr to EOF, fire progress callbacks, return error tail."""
        errs: list[str] = []
        async for raw in proc.stderr:
            text = raw.decode(errors="replace").rstrip()
            if not text:
                continue
            key, sep, value = text.partition("=")
            if sep and key in _PROGRESS_KEYS:
                if key == "out_time_us" and on_progress is not None:
                    try:
                        us = int(value)
                    except ValueError:
                        continue
                    if us >= 0:
                        on_progress(us / 1_000_000.0)
                continue
            errs.append(text)
        return "\n".join(errs[-5:])

    stderr_task = asyncio.create_task(drain_stderr())
    out = await proc.stdout.read()
    rc = await proc.wait()
    err_tail = await stderr_task

    if rc != 0:
        logger.debug(
            "idle-detector: ffmpeg failed for %s: %s",
            path.name,
            err_tail[:200],
        )
        return None
    if not out:
        return None
    n = len(out) // _FRAME_BYTES
    if n == 0:
        return None
    usable = n * _FRAME_BYTES
    return np.frombuffer(out[:usable], dtype=np.uint8).reshape(n, _FRAME_BYTES)
