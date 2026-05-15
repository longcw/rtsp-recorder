"""RecorderManager: coordinates the set of stream recorders + pruner task."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ConfigStore
from .models import Config, Stream, ServiceStatus
from .recorder import StreamRecorder

logger = logging.getLogger(__name__)

PRUNE_INTERVAL_SECONDS = 60 * 30  # every 30 minutes is plenty


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

    async def start(self) -> None:
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        cfg = await self.store.get()
        if cfg.running:
            await self.reconcile()
        self._prune_stop.clear()
        self._prune_task = asyncio.create_task(self._prune_loop(), name="pruner")

    async def shutdown(self) -> None:
        self._prune_stop.set()
        self._prune_wake.set()
        if self._prune_task:
            try:
                await asyncio.wait_for(self._prune_task, timeout=5)
            except asyncio.TimeoutError:
                self._prune_task.cancel()
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
            segment_seconds=cfg.segment_seconds,
            timezone=cfg.timezone,
            streams=statuses,
        )

    # ---- prune loop ----

    async def _prune_loop(self) -> None:
        while not self._prune_stop.is_set():
            try:
                cfg = await self.store.get()
                deleted = prune_old_files(self.recordings_dir, cfg.retention_days)
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


def prune_old_files(recordings_dir: Path, retention_days: int) -> int:
    """Delete files older than `retention_days` (by mtime). Returns count removed."""
    if not recordings_dir.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    for stream_dir in recordings_dir.iterdir():
        if not stream_dir.is_dir():
            continue
        for f in stream_dir.iterdir():
            if not f.is_file():
                continue
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            except FileNotFoundError:
                continue
            if mtime < cutoff:
                try:
                    f.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.warning("pruner: could not remove %s: %s", f, e)
    return removed
