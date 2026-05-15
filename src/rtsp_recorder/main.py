"""FastAPI app + REST API for managing the recorder service."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ConfigStore
from .manager import RecorderManager
from .models import (
    Config,
    RecordingFile,
    ServiceStatus,
    Stream,
)

logger = logging.getLogger(__name__)


# ---- request bodies ----

class StreamCreate(BaseModel):
    name: str
    url: str
    enabled: bool = True


class StreamUpdate(BaseModel):
    url: str | None = None
    enabled: bool | None = None


class RetentionUpdate(BaseModel):
    retention_days: int


class SegmentUpdate(BaseModel):
    segment_seconds: int


class TimezoneUpdate(BaseModel):
    timezone: str


# ---- helpers ----

_SEGMENT_NAME_FMT = "%Y-%m-%d_%H-%M-%S"


def _parse_segment_filename(name: str) -> datetime | None:
    """Parse a `YYYY-MM-DD_HH-MM-SS.<ext>` segment filename to a naive datetime.

    Returns None if the filename doesn't match — e.g. user-placed files. The
    result is naive on purpose: the filename carries no timezone, and we
    treat it as wall-clock in whatever zone the recorder was configured for
    when ffmpeg wrote it.
    """
    stem = name.rsplit(".", 1)[0]
    try:
        return datetime.strptime(stem, _SEGMENT_NAME_FMT)
    except ValueError:
        return None


# ---- app factory ----

def create_app(data_dir: Path | None = None) -> FastAPI:
    data_dir = data_dir or Path(os.environ.get("RTSP_RECORDER_DATA_DIR", "./data"))
    data_dir = data_dir.resolve()
    config_path = data_dir / "config.json"
    recordings_dir = data_dir / "recordings"

    store = ConfigStore(config_path)
    manager = RecorderManager(store, recordings_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await store.load()
        await manager.start()
        try:
            yield
        finally:
            await manager.shutdown()

    app = FastAPI(title="rtsp-recorder", lifespan=lifespan)

    # ---- service ----

    @app.get("/api/status", response_model=ServiceStatus)
    async def get_status() -> ServiceStatus:
        return await manager.status()

    @app.post("/api/start", response_model=ServiceStatus)
    async def start_service() -> ServiceStatus:
        await manager.set_running(True)
        return await manager.status()

    @app.post("/api/stop", response_model=ServiceStatus)
    async def stop_service() -> ServiceStatus:
        await manager.set_running(False)
        return await manager.status()

    # ---- streams ----

    @app.get("/api/streams", response_model=list[Stream])
    async def list_streams() -> list[Stream]:
        cfg = await store.get()
        return cfg.streams

    @app.post("/api/streams", response_model=Config, status_code=201)
    async def add_stream(body: StreamCreate) -> Config:
        try:
            return await manager.add_stream(
                Stream(name=body.name, url=body.url, enabled=body.enabled)
            )
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.patch("/api/streams/{name}", response_model=Config)
    async def patch_stream(name: str, body: StreamUpdate) -> Config:
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        if not updates:
            return await store.get()
        try:
            return await manager.update_stream(name, updates)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.delete("/api/streams/{name}", response_model=Config)
    async def delete_stream(name: str) -> Config:
        try:
            return await manager.remove_stream(name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    # ---- files ----

    def _resolve_stream_dir(name: str) -> Path:
        # Defence-in-depth: stream names are already validated by the Stream
        # model, but we still resolve and ensure the result stays under
        # recordings_dir.
        target = (recordings_dir / name).resolve()
        try:
            target.relative_to(recordings_dir)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid stream name")
        return target

    @app.get("/api/streams/{name}/files", response_model=list[RecordingFile])
    async def list_files(name: str) -> list[RecordingFile]:
        cfg = await store.get()
        if not any(s.name == name for s in cfg.streams):
            raise HTTPException(status_code=404, detail="stream not found")
        target = _resolve_stream_dir(name)
        if not target.exists():
            return []
        tz = ZoneInfo(cfg.timezone)
        files: list[RecordingFile] = []
        for f in target.iterdir():
            if not f.is_file():
                continue
            try:
                st = f.stat()
            except FileNotFoundError:
                continue
            modified_at = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            started_at_naive = _parse_segment_filename(f.name)
            duration_seconds: float | None = None
            if started_at_naive is not None:
                # Localize the parsed wall-clock time using the configured tz,
                # then take the delta against mtime (which is in UTC). Both
                # sides are tz-aware after localization so subtraction is
                # well-defined.
                start_aware = started_at_naive.replace(tzinfo=tz)
                duration_seconds = max(
                    0.0, (modified_at - start_aware).total_seconds()
                )
            files.append(
                RecordingFile(
                    name=f.name,
                    size=st.st_size,
                    modified_at=modified_at,
                    started_at=started_at_naive,
                    duration_seconds=duration_seconds,
                )
            )
        files.sort(key=lambda r: r.name, reverse=True)
        return files

    @app.get("/api/streams/{name}/files/{filename}")
    async def download_file(name: str, filename: str):
        if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
            raise HTTPException(status_code=400, detail="invalid filename")
        target = _resolve_stream_dir(name) / filename
        try:
            target.resolve().relative_to(recordings_dir)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid path")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(target, media_type="video/mp4", filename=filename)

    # ---- retention / config ----

    @app.get("/api/config", response_model=Config)
    async def get_config() -> Config:
        return await store.get()

    @app.put("/api/config/retention", response_model=Config)
    async def set_retention(body: RetentionUpdate) -> Config:
        if body.retention_days < 1:
            raise HTTPException(
                status_code=400, detail="retention_days must be >= 1"
            )
        return await manager.set_retention(body.retention_days)

    @app.put("/api/config/segment-seconds", response_model=Config)
    async def set_segment_seconds(body: SegmentUpdate) -> Config:
        if body.segment_seconds < 10 or body.segment_seconds > 3600:
            raise HTTPException(
                status_code=400,
                detail="segment_seconds must be between 10 and 3600",
            )
        return await manager.set_segment_seconds(body.segment_seconds)

    @app.put("/api/config/timezone", response_model=Config)
    async def set_timezone(body: TimezoneUpdate) -> Config:
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError, OSError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"unknown timezone {body.timezone!r}: {e}",
            ) from e
        return await manager.set_timezone(body.timezone)

    # ---- static frontend ----

    static_dir = Path(__file__).parent / "static"
    index_file = static_dir / "index.html"
    if index_file.is_file():
        # Serve the SPA. Assets live under /assets/... thanks to Vite's default
        # output layout. Everything else falls back to index.html so client-side
        # routes work.
        app.mount(
            "/assets",
            StaticFiles(directory=str(static_dir / "assets"), check_dir=False),
            name="assets",
        )

        @app.get("/")
        async def root() -> FileResponse:
            return FileResponse(index_file)

        @app.get("/{path:path}", include_in_schema=False)
        async def spa_fallback(path: str):
            if path.startswith("api/"):
                raise HTTPException(status_code=404)
            candidate = (static_dir / path).resolve()
            try:
                candidate.relative_to(static_dir.resolve())
            except ValueError:
                return FileResponse(index_file)
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_file)
    else:

        @app.get("/")
        async def root_no_ui():
            return RedirectResponse("/docs")

    return app


app = create_app()
