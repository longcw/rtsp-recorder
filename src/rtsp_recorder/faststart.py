"""Convert finalized fragmented-MP4 recordings to a faststart layout.

Segments are recorded as fragmented MP4 (``empty_moov + frag_keyframe``, see
``recorder._ffmpeg_args``) so the live segment is playable while ffmpeg is
still writing it. The downside: a fragmented file carries no real duration in
its header and no fragment index (``sidx``). A browser opening one has to walk
*every* ``moof`` box scattered across the whole file before it can learn the
duration or seek — i.e. it downloads the entire file before playout.

Once a segment is finalized we remux it (stream-copy, near-instant) into a
normal faststart MP4: a single ``moov`` carrying the real duration and the
sample/seek tables placed before ``mdat``. The browser then reads a few KB of
header and streams/seeks the rest via HTTP range requests.

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


def is_fragmented(path: Path) -> bool:
    """True if ``path`` is a fragmented MP4 (contains a top-level ``moof``).

    Walks only the top-level box headers — it reads 8–16 bytes per box and
    seeks over the payloads, so it never reads the (large) ``mdat`` data. A
    faststart file is just ``ftyp/moov/mdat`` so this returns after three
    cheap seeks; a fragmented file hits its first ``moof`` (the third box)
    immediately. Returns False on any parse/IO error — callers treat
    "unknown" as "leave it alone".
    """
    try:
        with open(path, "rb") as f:
            # Cap the walk; a well-formed faststart file has a handful of
            # top-level boxes, and we only need to reach the first moof.
            for _ in range(10_000):
                header = f.read(8)
                if len(header) < 8:
                    return False
                size = struct.unpack(">I", header[:4])[0]
                box_type = header[4:8]
                if box_type == b"moof":
                    return True
                if size == 1:
                    # 64-bit extended size follows the type.
                    ext = f.read(8)
                    if len(ext) < 8:
                        return False
                    size = struct.unpack(">Q", ext)[0]
                    payload = size - 16
                elif size == 0:
                    # Box runs to EOF — it's the last one.
                    return False
                else:
                    payload = size - 8
                if payload < 0:
                    return False
                f.seek(payload, os.SEEK_CUR)
        return False
    except OSError as e:
        logger.warning("faststart: could not inspect %s: %s", path, e)
        return False


async def ensure_faststart(path: Path) -> bool:
    """Remux ``path`` in place to a faststart MP4 if it is fragmented.

    Returns True if a conversion happened, False if the file was already
    faststart or the conversion failed (failures are logged; the original
    file is always left intact).
    """
    if not await asyncio.to_thread(is_fragmented, path):
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
