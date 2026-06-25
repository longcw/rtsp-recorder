"""Normalize finalized recordings for web playback (faststart + HEVC tag).

Segments are recorded as fragmented MP4 (``empty_moov + frag_keyframe``, see
``recorder._ffmpeg_args``) so the live segment is playable while ffmpeg is
still writing it. The downside: a fragmented file carries no real duration in
its header and no fragment index (``sidx``). A browser opening one has to walk
*every* ``moof`` box scattered across the whole file before it can learn the
duration or seek — i.e. it downloads the entire file before playout.

A second, codec-level problem hits HEVC cameras: ffmpeg's muxer tags HEVC
video as ``hev1`` (parameter sets in-band). Apple's decoders (Safari, iOS,
QuickTime) only play HEVC tagged ``hvc1`` (parameter sets out-of-band in the
``hvcC`` box); Chrome accepts both, so a recording plays on desktop Chrome but
shows the "unsupported" slash-play icon on an iPhone.

Once a segment is finalized we remux it (stream-copy, near-instant) to fix
both: a single ``moov`` carrying the real duration and the sample/seek tables
placed before ``mdat`` (faststart), and ``-tag:v hvc1`` for HEVC streams. The
browser then reads a few KB of header and streams/seeks the rest via HTTP
range requests, and Apple devices can decode it.

The remux is in-place and atomic (write to a temp file, ``os.replace``), and
the original mtime is restored so retention bookkeeping (which keys off mtime)
is unaffected.
"""
from __future__ import annotations

import asyncio
import logging
import os
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

# Suffix for the temp output. Must NOT end in ``.mp4`` so the analyzer and
# file-listing walks (which match ``*.mp4``) ignore an in-flight conversion.
_TMP_SUFFIX = ".faststart.tmp"

# Video sample-entry fourccs we recognize, searched for in the moov box.
_HEVC_FOURCCS = (b"hvc1", b"hev1")
_KNOWN_FOURCCS = (b"hvc1", b"hev1", b"avc1")


def _inspect(path: Path) -> tuple[bool, bytes | None]:
    """Return ``(fragmented, video_fourcc)`` for an MP4.

    Walks the top-level boxes (reading headers and seeking over payloads, so
    it never touches the large ``mdat`` bytes) to detect a ``moof`` box. When
    it reaches ``moov`` it reads that (small) box and scans it for a known
    video sample-entry fourcc. Returns ``(False, None)`` on any parse/IO
    error — callers treat "unknown" as "leave it alone".
    """
    fragmented = False
    fourcc: bytes | None = None
    try:
        with open(path, "rb") as f:
            for _ in range(10_000):
                header = f.read(8)
                if len(header) < 8:
                    break
                size = struct.unpack(">I", header[:4])[0]
                box_type = header[4:8]
                if size == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        break
                    size = struct.unpack(">Q", ext)[0]
                    payload = size - 16
                elif size == 0:
                    # Box runs to EOF — it's the last one.
                    payload = None
                else:
                    payload = size - 8
                if box_type == b"moof":
                    fragmented = True
                    # moov precedes moof in our files, so once we've also got
                    # the fourcc there's nothing left to learn.
                    if fourcc is not None:
                        break
                if box_type == b"moov":
                    data = f.read(payload if payload is not None else -1)
                    for fc in _KNOWN_FOURCCS:
                        if fc in data:
                            fourcc = fc
                            break
                    continue  # already advanced past the payload
                if payload is None or payload < 0:
                    break
                f.seek(payload, os.SEEK_CUR)
    except OSError as e:
        logger.warning("faststart: could not inspect %s: %s", path, e)
        return False, None
    return fragmented, fourcc


def is_fragmented(path: Path) -> bool:
    """True if ``path`` is a fragmented MP4 (contains a top-level ``moof``)."""
    return _inspect(path)[0]


async def ensure_faststart(path: Path) -> bool:
    """Remux ``path`` in place so it is web-playable, if it isn't already.

    Converts when the file is fragmented (needs faststart) or is HEVC tagged
    ``hev1`` (needs the Apple-compatible ``hvc1`` tag). Returns True if a
    conversion happened, False if the file was already fine or the conversion
    failed (failures are logged; the original file is always left intact).
    """
    fragmented, fourcc = await asyncio.to_thread(_inspect, path)
    is_hevc = fourcc in _HEVC_FOURCCS
    needs_faststart = fragmented
    needs_retag = fourcc == b"hev1"
    if not needs_faststart and not needs_retag:
        return False

    try:
        st = path.stat()
    except OSError as e:
        logger.warning("faststart: stat failed for %s: %s", path, e)
        return False

    tmp = path.with_name(path.name + _TMP_SUFFIX)
    args = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(path),
        "-c",
        "copy",
        # Apple decoders require HEVC tagged hvc1 (with out-of-band parameter
        # sets); this is a lossless re-tag, applied only to HEVC streams so
        # H.264 (avc1) recordings are untouched.
        *(["-tag:v", "hvc1"] if is_hevc else []),
        "-movflags",
        "+faststart",
        # The temp name doesn't end in .mp4, so ffmpeg can't infer the
        # container — state it explicitly.
        "-f",
        "mp4",
        str(tmp),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
    except OSError as e:
        logger.warning("faststart: failed to launch ffmpeg for %s: %s", path, e)
        _unlink_quietly(tmp)
        return False

    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        logger.warning(
            "faststart: remux failed for %s (rc=%s): %s",
            path,
            proc.returncode,
            stderr.decode(errors="replace").strip()[:500],
        )
        _unlink_quietly(tmp)
        return False

    try:
        os.replace(tmp, path)
        # Restore the original mtime so retention (which prunes by mtime)
        # treats the file as exactly as old as it really is.
        os.utime(path, (st.st_atime, st.st_mtime))
    except OSError as e:
        logger.warning("faststart: could not finalize %s: %s", path, e)
        _unlink_quietly(tmp)
        return False

    logger.info("faststart: converted %s", path.name)
    return True


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("faststart: could not remove temp %s: %s", path, e)
