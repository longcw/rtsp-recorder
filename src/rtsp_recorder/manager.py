"""RecorderManager: coordinates the set of stream recorders + pruner task."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import idle_index
from .config import ConfigStore
from .idle_detector import detect_idle
from .models import Config, Stream, ServiceStatus
from .recorder import StreamRecorder

logger = logging.getLogger(__name__)

PRUNE_INTERVAL_SECONDS = 60 * 30  # every 30 minutes is plenty
ANALYZE_INTERVAL_SECONDS = 30
# A file is considered "still being written" if its mtime advanced this
# recently. Skip such files in the analyzer to avoid reading a partial moov.
ANALYZE_MIN_AGE_SECONDS = 15


class RecorderManager:
    """Owns per-stream recorders and the background pruner.

    Config changes are applied by `reconcile()`: it diffs the current set of
    recorders against the desired set and starts/stops/replaces them as needed.
    """

    def __init__(self, store: ConfigStore, recordings_dir: Path) -> None:
        self.store = store
        self.recordings_dir = recordings_dir
        self._recorders: dict[str, StreamRecorder] = {}
        self._lock = asyncio.Lock()
        self._prune_task: asyncio.Task[None] | None = None
        self._prune_wake = asyncio.Event()
        self._prune_stop = asyncio.Event()
        self._analyze_task: asyncio.Task[None] | None = None
        self._analyze_stop = asyncio.Event()

    async def start(self) -> None:
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        cfg = await self.store.get()
        if cfg.running:
            await self.reconcile()
        self._prune_stop.clear()
        self._prune_task = asyncio.create_task(self._prune_loop(), name="pruner")
        self._analyze_stop.clear()
        self._analyze_task = asyncio.create_task(
            self._analyze_loop(), name="analyzer"
        )

    async def shutdown(self) -> None:
        self._prune_stop.set()
        self._prune_wake.set()
        self._analyze_stop.set()
        if self._prune_task:
            try:
                await asyncio.wait_for(self._prune_task, timeout=5)
            except asyncio.TimeoutError:
                self._prune_task.cancel()
        if self._analyze_task:
            try:
                await asyncio.wait_for(self._analyze_task, timeout=5)
            except asyncio.TimeoutError:
                self._analyze_task.cancel()
        async with self._lock:
            await asyncio.gather(
                *(r.stop() for r in self._recorders.values()), return_exceptions=True
            )
            self._recorders.clear()

    # ---- public mutation ----

    async def set_running(self, running: bool) -> Config:
        cfg = await self.store.get()
        cfg.running = running
        cfg = await self.store.update(cfg)
        await self.reconcile()
        return cfg

    async def set_retention(self, days: int) -> Config:
        cfg = await self.store.get()
        cfg.retention_days = days
        cfg = await self.store.update(cfg)
        # Run a prune pass soon to apply the new value.
        self._prune_wake.set()
        return cfg

    async def set_idle_retention(self, days: int) -> Config:
        cfg = await self.store.get()
        cfg.idle_retention_days = days
        cfg = await self.store.update(cfg)
        self._prune_wake.set()
        return cfg

    async def set_file_idle(self, stream_name: str, filename: str, idle: bool) -> None:
        """Manually override a recording's idle flag."""
        target_dir = self.recordings_dir / stream_name
        if not (target_dir / filename).is_file():
            raise KeyError(f"file '{filename}' not found in stream '{stream_name}'")
        await asyncio.to_thread(idle_index.set_idle, target_dir, filename, idle)
        # Newly-flagged-idle file may now be due for an earlier prune; wake the
        # pruner so the change is visible quickly.
        self._prune_wake.set()

    async def set_segment_seconds(self, seconds: int) -> Config:
        cfg = await self.store.get()
        cfg.segment_seconds = seconds
        cfg = await self.store.update(cfg)
        # Reconcile will recreate recorders so the new segment length takes
        # effect on the next ffmpeg launch.
        await self.reconcile()
        return cfg

    async def set_timezone(self, tz: str) -> Config:
        cfg = await self.store.get()
        cfg.timezone = tz
        cfg = await self.store.update(cfg)
        # Recorders need a restart so the new TZ propagates to ffmpeg's
        # strftime (filenames).
        await self.reconcile()
        return cfg

    async def add_stream(self, stream: Stream) -> Config:
        cfg = await self.store.get()
        if any(s.name == stream.name for s in cfg.streams):
            raise ValueError(f"stream '{stream.name}' already exists")
        cfg.streams.append(stream)
        cfg = await self.store.update(cfg)
        await self.reconcile()
        return cfg

    async def remove_stream(self, name: str) -> Config:
        cfg = await self.store.get()
        before = len(cfg.streams)
        cfg.streams = [s for s in cfg.streams if s.name != name]
        if len(cfg.streams) == before:
            raise KeyError(f"stream '{name}' not found")
        cfg = await self.store.update(cfg)
        await self.reconcile()
        return cfg

    async def update_stream(self, name: str, updates: dict) -> Config:
        cfg = await self.store.get()
        for i, s in enumerate(cfg.streams):
            if s.name == name:
                cfg.streams[i] = s.model_copy(update=updates)
                break
        else:
            raise KeyError(f"stream '{name}' not found")
        cfg = await self.store.update(cfg)
        await self.reconcile()
        return cfg

    # ---- reconcile ----

    async def reconcile(self) -> None:
        """Drive the live recorder set toward the persisted config."""
        async with self._lock:
            cfg = await self.store.get()
            desired: dict[str, Stream] = (
                {s.name: s for s in cfg.streams} if cfg.running else {}
            )

            # Stop recorders that should no longer be running, or whose
            # config (url, segment length, timezone) has changed under them.
            stop_targets: list[StreamRecorder] = []
            for name, rec in list(self._recorders.items()):
                wanted = desired.get(name)
                if (
                    wanted is None
                    or not wanted.enabled
                    or wanted.url != rec.stream.url
                    or cfg.segment_seconds != rec.segment_seconds
                    or cfg.timezone != rec.tz
                ):
                    stop_targets.append(rec)
                    self._recorders.pop(name)
            if stop_targets:
                await asyncio.gather(
                    *(r.stop() for r in stop_targets), return_exceptions=True
                )

            # Start recorders that should be running but aren't.
            for name, s in desired.items():
                if not s.enabled:
                    continue
                if name in self._recorders:
                    # url + segment unchanged (caught above) and enabled —
                    # leave alone.
                    continue
                rec = StreamRecorder(
                    s,
                    self.recordings_dir,
                    cfg.segment_seconds,
                    cfg.timezone,
                )
                self._recorders[name] = rec
                rec.start()

    # ---- status ----

    async def status(self) -> ServiceStatus:
        cfg = await self.store.get()
        statuses = []
        by_name = self._recorders
        for s in cfg.streams:
            rec = by_name.get(s.name)
            if rec is not None:
                statuses.append(rec.status())
            else:
                statuses.append(
                    rec_status_for_inactive(s, running=cfg.running)
                )
        return ServiceStatus(
            running=cfg.running,
            retention_days=cfg.retention_days,
            idle_retention_days=cfg.idle_retention_days,
            segment_seconds=cfg.segment_seconds,
            timezone=cfg.timezone,
            streams=statuses,
        )

    # ---- prune loop ----

    async def _prune_loop(self) -> None:
        while not self._prune_stop.is_set():
            try:
                cfg = await self.store.get()
                # Idle retention must not exceed the regular retention — at the
                # API level we accept any valid value; clamp here so nonsensical
                # combinations (idle_retention > retention) collapse to "treat
                # idle the same as normal" rather than keeping idle files
                # *longer* than busy ones.
                idle_days = min(cfg.idle_retention_days, cfg.retention_days)
                deleted = prune_old_files(
                    self.recordings_dir, cfg.retention_days, idle_days
                )
                if deleted:
                    logger.info("pruner: removed %d old files", deleted)
            except Exception:
                logger.exception("pruner: failed")

            self._prune_wake.clear()
            try:
                await asyncio.wait_for(
                    self._prune_wake.wait(), timeout=PRUNE_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass

    # ---- analyzer loop ----

    async def _analyze_loop(self) -> None:
        """Scan finalized segments and tag them as idle/not-idle.

        Each tick walks every stream directory, finds .mp4 files that have
        no entry in the per-stream idle index, and analyzes them one at a
        time. Skips files whose mtime advanced recently — those are either
        the live segment or one that just rotated, and we want a settled
        moov before reading.
        """
        # Small initial delay so we don't fight startup work.
        try:
            await asyncio.wait_for(self._analyze_stop.wait(), timeout=5)
            return
        except asyncio.TimeoutError:
            pass

        while not self._analyze_stop.is_set():
            try:
                await self._analyze_pending()
            except Exception:
                logger.exception("analyzer: tick failed")

            try:
                await asyncio.wait_for(
                    self._analyze_stop.wait(), timeout=ANALYZE_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass

    async def _analyze_pending(self) -> None:
        if not self.recordings_dir.exists():
            return
        now = time.time()
        for stream_dir in self.recordings_dir.iterdir():
            if not stream_dir.is_dir():
                continue
            index = await asyncio.to_thread(idle_index.load, stream_dir)
            for f in sorted(stream_dir.iterdir()):
                if self._analyze_stop.is_set():
                    return
                if not f.is_file() or not f.name.endswith(".mp4"):
                    continue
                if f.name in index:
                    continue
                try:
                    st = f.stat()
                except FileNotFoundError:
                    continue
                if now - st.st_mtime < ANALYZE_MIN_AGE_SECONDS:
                    continue
                result = await detect_idle(f)
                if result is None:
                    # Unanalyzable (decode error, too short). Skip — leave
                    # absent from the index so we'll retry next tick; if the
                    # file is genuinely broken we'll burn cycles forever,
                    # but that's bounded by the file count and prune horizon.
                    continue
                await asyncio.to_thread(
                    idle_index.set_idle, stream_dir, f.name, result
                )
                logger.debug(
                    "analyzer: %s/%s -> idle=%s",
                    stream_dir.name,
                    f.name,
                    result,
                )


def rec_status_for_inactive(stream: Stream, *, running: bool):
    from .models import StreamStatus

    if not running:
        state = "stopped"
    elif not stream.enabled:
        state = "stopped"
    else:
        state = "stopped"  # not yet started by reconcile
    return StreamStatus(
        name=stream.name,
        url=stream.url,
        enabled=stream.enabled,
        state=state,  # type: ignore[arg-type]
    )


def prune_old_files(
    recordings_dir: Path,
    retention_days: int,
    idle_retention_days: int,
) -> int:
    """Delete files older than the applicable retention.

    Files flagged as idle in the per-stream `.idle.json` index use
    `idle_retention_days`; everything else uses `retention_days`. Returns
    the number of files removed.
    """
    if not recordings_dir.exists():
        return 0
    now = datetime.now(timezone.utc)
    busy_cutoff = now - timedelta(days=retention_days)
    idle_cutoff = now - timedelta(days=idle_retention_days)
    removed = 0
    for stream_dir in recordings_dir.iterdir():
        if not stream_dir.is_dir():
            continue
        index = idle_index.load(stream_dir)
        present: set[str] = set()
        for f in stream_dir.iterdir():
            if not f.is_file():
                continue
            # Don't try to prune the index file itself.
            if f.name == idle_index.INDEX_FILENAME:
                continue
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            except FileNotFoundError:
                continue
            entry = index.get(f.name)
            is_idle = isinstance(entry, dict) and entry.get("idle") is True
            cutoff = idle_cutoff if is_idle else busy_cutoff
            if mtime < cutoff:
                try:
                    f.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.warning("pruner: could not remove %s: %s", f, e)
            else:
                present.add(f.name)
        # Drop index entries for files we just removed (or that vanished).
        idle_index.drop_missing(stream_dir, present)
    return removed
