"""Serve a live fragmented segment with a segment index (``sidx``) spliced in.

The live segment is a fragmented MP4 (see ``faststart``) whose header carries
no duration and no fragment index, so a browser's demuxer walks every
``moof`` in the file before playout — on a near-full hourly segment that is
the whole ~250 MB. A ``sidx`` box covering every fragment gives it the
duration and seek points up front, so it stops after the first fragment and
range-requests only what it plays, like a finalized (faststart) file.

The view is a snapshot cut at a fragment boundary: ``ftyp`` + ``moov``, the
built ``sidx``, then every complete ``moof``/``mdat`` pair below a byte
limit. The client names that limit, so every range request against one
snapshot sees the same bytes while ffmpeg keeps appending. Fragment data
offsets are relative to their ``moof`` (``default_base_moof``), so inserting
bytes ahead of the first ``moof`` leaves them valid.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# A piece of the served view: literal bytes, or (file offset, length).
Piece = bytes | tuple[int, int]

# Boxes descended into while reading the moov.
_MOOV_CONTAINERS = (b"moov", b"trak", b"mdia", b"mvex")


def _boxes(f: BinaryIO, start: int, end: int):
    """Yield ``(type, box_start, payload_start, box_end)`` for boxes in a span."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        header = f.read(8)
        if len(header) < 8:
            return
        size, box_type = struct.unpack(">I4s", header)
        payload = pos + 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                return
            size = struct.unpack(">Q", ext)[0]
            payload = pos + 16
        elif size == 0:
            size = end - pos
        if size < payload - pos:
            return
        yield box_type, pos, payload, pos + size
        pos += size


def _read_moov(f: BinaryIO, start: int, end: int) -> tuple[int, int, int] | None:
    """Return ``(track_id, timescale, trex default duration)`` of the video track."""
    tracks: list[dict] = []
    trex: dict[int, int] = {}

    def walk(s: int, e: int, track: dict | None) -> None:
        for box_type, _, payload, box_end in _boxes(f, s, e):
            if box_type == b"trak":
                track = {}
                tracks.append(track)
                walk(payload, box_end, track)
                continue
            if box_type in _MOOV_CONTAINERS:
                walk(payload, box_end, track)
                continue
            f.seek(payload)
            body = f.read(min(box_end - payload, 32))
            if track is not None and box_type == b"tkhd":
                track["id"] = struct.unpack_from(">I", body, 20 if body[0] == 1 else 12)[0]
            elif track is not None and box_type == b"mdhd":
                track["timescale"] = struct.unpack_from(">I", body, 20 if body[0] == 1 else 12)[0]
            elif track is not None and box_type == b"hdlr":
                track["handler"] = body[8:12]
            elif box_type == b"trex":
                track_id, _, duration = struct.unpack_from(">III", body, 4)
                trex[track_id] = duration

    walk(start, end, None)
    for t in tracks:
        if t.get("handler") == b"vide" and "id" in t and t.get("timescale"):
            return t["id"], t["timescale"], trex.get(t["id"], 0)
    return None


def _read_fragment(moof: bytes, track_id: int, trex_duration: int) -> tuple[int, int] | None:
    """Return ``(base decode time, duration)`` of the video track in one ``moof``."""
    pos = 8
    while pos + 8 <= len(moof):
        size, box_type = struct.unpack_from(">I4s", moof, pos)
        if size < 8:
            return None
        if box_type == b"traf":
            traf = moof[pos + 8 : pos + size]
            tfhd_id = base = None
            default_duration = trex_duration
            duration = 0
            p = 0
            while p + 8 <= len(traf):
                bsize, btype = struct.unpack_from(">I4s", traf, p)
                if bsize < 8:
                    return None
                body = traf[p + 8 : p + bsize]
                flags = int.from_bytes(body[1:4], "big")
                if btype == b"tfhd":
                    tfhd_id = struct.unpack_from(">I", body, 4)[0]
                    if tfhd_id != track_id:
                        break
                    off = 8 + (8 if flags & 0x01 else 0) + (4 if flags & 0x02 else 0)
                    if flags & 0x08:
                        default_duration = struct.unpack_from(">I", body, off)[0]
                elif btype == b"tfdt":
                    base = struct.unpack_from(">Q" if body[0] == 1 else ">I", body, 4)[0]
                elif btype == b"trun":
                    count = struct.unpack_from(">I", body, 4)[0]
                    off = 8 + (4 if flags & 0x01 else 0) + (4 if flags & 0x04 else 0)
                    if flags & 0x100:
                        stride = 4 * bin(flags & 0xF00).count("1")
                        duration += sum(
                            struct.unpack_from(">I", body, off + i * stride)[0] for i in range(count)
                        )
                    else:
                        duration += count * default_duration
                p += bsize
            if tfhd_id == track_id and base is not None:
                return base, duration
        pos += size
    return None


@dataclass
class LiveIndex:
    """Complete fragments of a live segment, below a byte limit."""

    head_end: int  # end of the moov; the first moof starts here
    track_id: int
    timescale: int
    # (start, end, base decode time, duration) per moof/mdat pair
    fragments: list[tuple[int, int, int, int]]

    @property
    def seconds(self) -> float:
        last = self.fragments[-1]
        return (last[2] + last[3] - self.fragments[0][2]) / self.timescale

    def view(self) -> tuple[list[Piece], int]:
        """Return the pieces of the indexed file and its total size."""
        frags = self.fragments
        # durations come from consecutive decode times, the last from its samples
        refs = b"".join(
            struct.pack(
                ">III",
                end - start,
                (frags[i + 1][2] - base) if i + 1 < len(frags) else duration,
                0x90000000,  # starts_with_SAP=1, SAP type 1
            )
            for i, (start, end, base, duration) in enumerate(frags)
        )
        body = (
            struct.pack(">I", 1 << 24)  # version 1, flags 0
            + struct.pack(">IIQQHH", self.track_id, self.timescale, frags[0][2], 0, 0, len(frags))
            + refs
        )
        box = struct.pack(">I4s", 8 + len(body), b"sidx") + body
        tail = frags[-1][1]
        return [(0, self.head_end), box, (self.head_end, tail - self.head_end)], tail + len(box)


def read_index(path: Path, limit: int) -> LiveIndex | None:
    """Index the fragments below ``limit``, or None if the file is not fragmented."""
    with open(path, "rb") as f:
        head_end = None
        track = None
        fragments: list[tuple[int, int, int, int]] = []
        pending: tuple[int, int, int] | None = None  # moof awaiting its mdat
        for box_type, start, payload, end in _boxes(f, 0, limit):
            if end > limit:
                break
            if box_type == b"moov":
                track = _read_moov(f, payload, end)
                head_end = end
            elif box_type == b"moof":
                if track is None:
                    return None
                f.seek(start)
                info = _read_fragment(f.read(end - start), track[0], track[2])
                if info is None:
                    return None
                pending = (start, *info)
            elif box_type == b"mdat" and pending is not None:
                fragments.append((pending[0], end, pending[1], pending[2]))
                pending = None
            elif head_end is None:
                continue
            else:
                # e.g. the mfra ffmpeg appends when it closes the segment
                break

    if head_end is None or track is None or not fragments or len(fragments) > 0xFFFF:
        return None
    if fragments[0][0] != head_end:
        return None
    return LiveIndex(head_end, track[0], track[1], fragments)
