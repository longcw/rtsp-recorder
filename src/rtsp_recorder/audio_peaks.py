"""Audio peak-envelope extraction for finished recordings.

Decodes a segment's audio to mono PCM and reduces it to a short array of
per-bucket peak levels, which the web UI draws as a waveform so sound events
are visible without scrubbing the video.

Levels are dBFS on a fixed scale rather than normalized per file. A quiet
recording has to look quiet next to a loud one, otherwise the per-recording
thumbnails in the file list cannot be compared against each other.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .idle_detector import probe_duration

logger = logging.getLogger(__name__)

# Cameras send narrowband audio (see `recorder.py`, which no longer resamples
# on the way in), so decoding at 8 kHz mono costs nothing and pins the
# sample-to-time mapping without a second probe.
_SAMPLE_RATE = 8000

# One bucket every half second, floored so a 10 s segment still gets a usable
# shape and capped so an hour-long one stays a few hundred bytes.
_BUCKETS_PER_SECOND = 2
_MIN_BUCKETS = 60
_MAX_BUCKETS = 600

# Quieter than this is drawn as silence. Camera preamps idle around -60 dBFS,
# so nothing below carries information.
_FLOOR_DB = -60.0

_FULL_SCALE = 32767.0


@dataclass
class PeaksResult:
    # 0..255 per bucket. None when the recording has no usable audio track.
    peaks: list[int] | None
    duration_seconds: float | None


async def analyze_audio(path: Path) -> PeaksResult:
    """Probe `path` for its audio waveform.

    Raises FileNotFoundError when ffmpeg/ffprobe are missing, so a broken
    install retries instead of marking every recording as silent.
    """
    duration = await probe_duration(path)
    if not await _has_audio_stream(path):
        return PeaksResult(peaks=None, duration_seconds=duration)
    samples = await _decode_pcm(path)
    if samples is None or samples.size == 0:
        return PeaksResult(peaks=None, duration_seconds=duration)
    audio_seconds = samples.size / _SAMPLE_RATE
    return PeaksResult(
        peaks=peaks_from_samples(samples, audio_seconds),
        duration_seconds=duration if duration is not None else audio_seconds,
    )


def peaks_from_samples(samples: np.ndarray, seconds: float) -> list[int]:
    """Reduce mono int16 PCM to per-bucket levels on the fixed dBFS scale."""
    n = min(_MAX_BUCKETS, max(_MIN_BUCKETS, round(seconds * _BUCKETS_PER_SECOND)))
    n = min(n, samples.size)
    if n <= 0:
        return []
    # Bucket by edges rather than reshaping: the sample count rarely divides
    # evenly, and dropping the remainder would lop the tail off the clip.
    edges = np.linspace(0, samples.size, n + 1).astype(np.int64)
    amp = np.abs(samples.astype(np.int32))
    peaks = np.maximum.reduceat(amp, edges[:-1])
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(peaks / _FULL_SCALE)
    level = (db - _FLOOR_DB) / (-_FLOOR_DB) * 255.0
    return [int(v) for v in np.clip(level, 0.0, 255.0).round()]


def encode(peaks: list[int]) -> str:
    return base64.b64encode(bytes(peaks)).decode("ascii")


def downsample(encoded: str, buckets: int) -> str | None:
    """Re-bucket an encoded peak array down to `buckets` values.

    Returns None if the input can't be read, so one damaged entry can't take
    the whole file list down with it.
    """
    try:
        raw = np.frombuffer(base64.b64decode(encoded, validate=True), dtype=np.uint8)
    except Exception:
        logger.debug("audio-peaks: undecodable peaks entry", exc_info=True)
        return None
    if raw.size == 0:
        return None
    if raw.size <= buckets:
        return encoded
    edges = np.linspace(0, raw.size, buckets + 1).astype(np.int64)
    out = np.maximum.reduceat(raw, edges[:-1])
    return base64.b64encode(out.astype(np.uint8).tobytes()).decode("ascii")


async def _has_audio_stream(path: Path) -> bool:
    args = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return False
    return out.decode(errors="replace").strip() == "audio"


async def _decode_pcm(path: Path) -> np.ndarray | None:
    """Decode the first audio track to mono int16 at `_SAMPLE_RATE`."""
    args = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-i", str(path),
        "-map", "0:a:0",
        "-vn",
        "-ac", "1",
        "-ar", str(_SAMPLE_RATE),
        "-f", "s16le",
        "-",
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stderr is not None
    # Drain stderr concurrently — a full error pipe would deadlock the decoder
    # before it reaches EOF on stdout.
    stderr_task = asyncio.create_task(proc.stderr.read())
    out = await proc.stdout.read()
    rc = await proc.wait()
    err = await stderr_task
    if rc != 0:
        logger.debug(
            "audio-peaks: ffmpeg failed for %s: %s",
            path.name,
            err.decode(errors="replace")[-200:],
        )
        return None
    if not out:
        return None
    # A truncated final sample would misalign the whole int16 view.
    usable = len(out) - (len(out) % 2)
    return np.frombuffer(out[:usable], dtype=np.int16)
