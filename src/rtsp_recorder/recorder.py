"""Per-stream ffmpeg recorder.

Each `StreamRecorder` manages one ffmpeg subprocess that records its RTSP source
into rotating 1-minute MP4 files. ffmpeg's `segment` muxer handles the rotation
natively (atomic file finalization, no re-muxing in Python) and `strftime`
generates human-readable filenames.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import Stream, StreamStatus

logger = logging.getLogger(__name__)

# Time we wait between restart attempts after ffmpeg exits with a failure.
# Keep small so transient network blips recover quickly; an actually-broken
# stream just keeps logging restart attempts, which is fine.
RESTART_BACKOFF_SECONDS = 5.0


class StreamRecorder:
    """Manages one RTSP -> rotating mp4 ffmpeg subprocess."""

    def __init__(
        self, stream: Stream, base_dir: Path, segment_seconds: int
    ) -> None:
        self.stream = stream
        self.segment_seconds = segment_seconds
        self.dir = base_dir / stream.name
        self._task: asyncio.Task[None] | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._stop = asyncio.Event()
        self._state: str = "stopped"
        self._started_at: datetime | None = None
        self._last_error: str | None = None
        self._restart_count: int = 0
        self._current_file: str | None = None
        # Once True, subsequent failures present as "reconnecting" rather than
        # "error" so the UI can distinguish never-worked from lost-connection.
        self._has_recorded: bool = False

    # ---- lifecycle ----

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._state = "starting"
        self._task = asyncio.create_task(
            self._run(), name=f"recorder:{self.stream.name}"
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                if self._proc and self._proc.returncode is None:
                    self._proc.kill()
                try:
                    await asyncio.wait_for(self._task, timeout=5)
                except asyncio.TimeoutError:
                    logger.error(
                        "recorder %s: task did not exit after kill", self.stream.name
                    )
        self._state = "stopped"
        self._started_at = None
        self._current_file = None

    # ---- status ----

    def status(self) -> StreamStatus:
        return StreamStatus(
            name=self.stream.name,
            url=self.stream.url,
            enabled=self.stream.enabled,
            state=self._state,  # type: ignore[arg-type]
            started_at=self._started_at,
            last_error=self._last_error,
            restart_count=self._restart_count,
            current_file=self._current_file,
        )

    # ---- internals ----

    async def _run(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        first_attempt = True
        while not self._stop.is_set():
            self._state = "starting" if first_attempt else "reconnecting"
            self._started_at = datetime.now(timezone.utc)
            try:
                code = await self._run_ffmpeg_once()
            except Exception as e:  # pragma: no cover - defensive
                logger.exception("recorder %s: unexpected error", self.stream.name)
                self._last_error = f"unexpected: {e}"
                code = -1

            if self._stop.is_set():
                break

            if code == 0:
                # ffmpeg exited cleanly without us asking — unusual for a live
                # stream. Treat as a failure so we retry.
                self._last_error = "ffmpeg exited cleanly; restarting"
            self._current_file = None
            # Have we ever successfully recorded? If so this is a reconnection
            # attempt; if not, present as a hard error so the UI signals
            # "configuration likely wrong."
            self._state = "reconnecting" if self._has_recorded else "error"
            self._restart_count += 1
            first_attempt = False
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=RESTART_BACKOFF_SECONDS
                )
                break
            except asyncio.TimeoutError:
                pass

        self._state = "stopped"
        self._current_file = None

    async def _run_ffmpeg_once(self) -> int:
        pattern = str(self.dir / "%Y-%m-%d_%H-%M-%S.mp4")
        args = self._ffmpeg_args(pattern)
        logger.info("recorder %s: starting ffmpeg", self.stream.name)
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        # Pump stderr in the background to track current file + last error and
        # avoid the pipe filling up.
        assert self._proc.stderr is not None
        stderr_task = asyncio.create_task(self._consume_stderr(self._proc.stderr))
        try:
            code = await self._proc.wait()
        finally:
            # Drain remaining stderr before tearing down. ffmpeg's last lines
            # (the failure cause, or the final segment-Opening) typically arrive
            # right before exit; cancelling immediately would lose them and
            # leave us with stale state. Cap the wait so a misbehaving child
            # cannot wedge shutdown.
            try:
                await asyncio.wait_for(stderr_task, timeout=2.0)
            except asyncio.TimeoutError:
                stderr_task.cancel()
                try:
                    await stderr_task
                except (asyncio.CancelledError, Exception):
                    pass
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "recorder %s: stderr task crashed", self.stream.name
                )
        logger.info("recorder %s: ffmpeg exited with code %s", self.stream.name, code)
        return code

    def _ffmpeg_args(self, pattern: str) -> list[str]:
        return [
            "ffmpeg",
            "-hide_banner",
            # info level emits "Opening 'file' for writing" per segment, which
            # we parse to flip state -> recording. `level+` prefixes each line
            # with its severity so we can distinguish real errors from info
            # output that incidentally contains the word "error".
            "-loglevel",
            "level+info",
            "-nostdin",
            "-rtsp_transport",
            "tcp",
            "-i",
            self.stream.url,
            "-c",
            "copy",
            "-an",  # no audio for now; many RTSP cameras have problematic audio
            "-f",
            "segment",
            "-segment_time",
            str(self.segment_seconds),
            "-segment_format",
            "mp4",
            # Write each segment as a fragmented mp4 so it is playable while
            # ffmpeg is still writing it (each fragment carries its own header).
            # `empty_moov` writes an initial moov at the start of the file,
            # `frag_keyframe` starts a new fragment on each keyframe, and
            # `default_base_moof` keeps fragments self-contained.
            # `flush_packets=1` forces the inner mp4 muxer to flush each
            # fragment to disk; without it ffmpeg keeps the bytes in its
            # seekable-file write buffer and the partial file stays
            # unplayable until the segment closes.
            "-segment_format_options",
            "movflags=+empty_moov+default_base_moof+frag_keyframe:flush_packets=1",
            "-segment_atclocktime",
            "1",
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            pattern,
        ]

    _OPENING_RE = re.compile(r"Opening '([^']+)' for writing")
    # With `-loglevel level+info` every ffmpeg log line is prefixed with the
    # level in brackets, e.g. "[info] ...", "[error] ...".
    _LEVEL_RE = re.compile(r"\[(fatal|error|warning|info|verbose|debug)\]\s*(.*)")

    async def _consume_stderr(self, stream: asyncio.StreamReader) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode(errors="replace").rstrip()
            level_match = self._LEVEL_RE.search(text)
            level = level_match.group(1) if level_match else "info"
            body = level_match.group(2) if level_match else text

            opening = self._OPENING_RE.search(body)
            if opening:
                self._current_file = Path(opening.group(1)).name
                self._state = "recording"
                self._has_recorded = True
                self._last_error = None
            elif level in ("fatal", "error"):
                self._last_error = body
            logger.debug("ffmpeg[%s][%s]: %s", self.stream.name, level, body)
