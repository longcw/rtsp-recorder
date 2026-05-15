"""FastAPI app + REST API for managing the recorder service."""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import re

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import idle_index
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


class IdleRetentionUpdate(BaseModel):
    idle_retention_days: int


class MotionThresholdUpdate(BaseModel):
    motion_threshold: float


class FileIdleUpdate(BaseModel):
    idle: bool


class SegmentUpdate(BaseModel):
    segment_seconds: int


class TimezoneUpdate(BaseModel):
    timezone: str


# ---- helpers ----

_SEGMENT_NAME_FMT = "%Y-%m-%d_%H-%M-%S"

# Single-range only. We don't bother with multi-range responses — browsers
# only send a single bytes=N-M when streaming a <video>, and supporting
# multipart/byteranges would require a substantially more involved encoder.
_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d*)$")
_RANGE_CHUNK = 64 * 1024


def _range_not_satisfiable(file_size: int) -> StreamingResponse:
    return StreamingResponse(
        iter([b""]),
        status_code=416,
        headers={"Content-Range": f"bytes */{file_size}"},
    )


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
        idle_data = idle_index.load(target)
        analyzing_now = manager.analyzing_file(name)
        analyzing_name = analyzing_now[0] if analyzing_now else None
        analyzing_progress = analyzing_now[1] if analyzing_now else None
        files: list[RecordingFile] = []
        for f in target.iterdir():
            if not f.is_file():
                continue
            if f.name == idle_index.INDEX_FILENAME:
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
            entry = idle_data.get(f.name)
            idle = entry.get("idle") if isinstance(entry, dict) else None
            # Prefer ffprobe-derived duration when we have one. The
            # mtime-minus-filename fallback is noisy because segment
            # cuts only happen on keyframes — actual video can run
            # seconds past the nominal boundary.
            cached_duration = entry.get("duration") if isinstance(entry, dict) else None
            if isinstance(cached_duration, (int, float)) and cached_duration >= 0:
                duration_seconds = float(cached_duration)
            is_analyzing = (f.name == analyzing_name)
            files.append(
                RecordingFile(
                    name=f.name,
                    size=st.st_size,
                    modified_at=modified_at,
                    started_at=started_at_naive,
                    duration_seconds=duration_seconds,
                    idle=idle if isinstance(idle, bool) else None,
                    analyzing=is_analyzing,
                    analyze_progress=analyzing_progress if is_analyzing else None,
                )
            )
        files.sort(key=lambda r: r.name, reverse=True)
        return files

    @app.post("/api/streams/{name}/files/{filename}/reanalyze", response_model=dict)
    async def reanalyze_file(name: str, filename: str) -> dict:
        if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
            raise HTTPException(status_code=400, detail="invalid filename")
        try:
            await manager.reanalyze_file(name, filename)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"name": filename}

    @app.post("/api/streams/{name}/reanalyze-idle", response_model=dict)
    async def reanalyze_idle(name: str) -> dict:
        cfg = await store.get()
        if not any(s.name == name for s in cfg.streams):
            raise HTTPException(status_code=404, detail="stream not found")
        try:
            dropped = await manager.reanalyze_stream(name)
        except KeyError:
            # No recordings dir yet — nothing to drop, but answer success
            # so the UI doesn't show a scary error in the empty-state case.
            dropped = 0
        return {"dropped": dropped}

    @app.delete("/api/streams/{name}/files/{filename}", response_model=dict)
    async def delete_recording(name: str, filename: str) -> dict:
        if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
            raise HTTPException(status_code=400, detail="invalid filename")
        # Defence-in-depth: ensure the resolved path stays inside the stream's
        # own recordings dir before we unlink anything.
        target = _resolve_stream_dir(name) / filename
        try:
            target.resolve().relative_to(recordings_dir)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid path")
        try:
            await manager.delete_file(name, filename)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"name": filename, "deleted": True}

    @app.patch("/api/streams/{name}/files/{filename}", response_model=dict)
    async def patch_file(name: str, filename: str, body: FileIdleUpdate) -> dict:
        if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
            raise HTTPException(status_code=400, detail="invalid filename")
        try:
            await manager.set_file_idle(name, filename, body.idle)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"name": filename, "idle": body.idle}

    @app.get("/api/streams/{name}/files/{filename}")
    async def download_file(name: str, filename: str, request: Request):
        if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
            raise HTTPException(status_code=400, detail="invalid filename")
        target = _resolve_stream_dir(name) / filename
        try:
            target.resolve().relative_to(recordings_dir)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid path")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not found")

        file_size = target.stat().st_size
        range_header = request.headers.get("range")
        if range_header:
            m = _RANGE_RE.match(range_header.strip())
            if not m:
                # Per RFC 9110, an unparseable Range should be ignored, but
                # we'd rather be explicit so misbehaving clients notice.
                raise HTTPException(status_code=400, detail="invalid Range header")
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else file_size - 1
            if start >= file_size or start > end:
                return _range_not_satisfiable(file_size)
            end = min(end, file_size - 1)
            length = end - start + 1

            def iter_range():
                # Re-open + seek for each request; OS page cache handles
                # the hot-path. Chunking caps memory and lets uvicorn flush
                # progressively while the client buffers ahead.
                with open(target, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        data = f.read(min(_RANGE_CHUNK, remaining))
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            return StreamingResponse(
                iter_range(),
                status_code=206,
                media_type="video/mp4",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(length),
                    # Keep the filename hint so explicit downloads still get
                    # a nice name. The browser ignores Content-Disposition
                    # for media elements, so <video> playback is unaffected.
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )

        return FileResponse(
            target,
            media_type="video/mp4",
            filename=filename,
            headers={"Accept-Ranges": "bytes"},
        )

    @app.get("/api/streams/{name}/files/{filename}/clip")
    async def clip_file(
        name: str,
        filename: str,
        start: float = Query(..., ge=0.0),
        end: float = Query(..., gt=0.0),
    ):
        if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
            raise HTTPException(status_code=400, detail="invalid filename")
        if end <= start:
            raise HTTPException(status_code=400, detail="end must be greater than start")
        # Cap clip duration. An hour is plenty for any "find the interesting bit"
        # workflow and stops a fat-fingered drag from spinning ffmpeg on a
        # multi-hour span.
        if end - start > 3600:
            raise HTTPException(
                status_code=400, detail="clip duration must be <= 1 hour"
            )
        target = _resolve_stream_dir(name) / filename
        try:
            target.resolve().relative_to(recordings_dir)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid path")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not found")

        # Write into a per-clip tempfile and stream it back, then delete via
        # the background task. We can't pipe ffmpeg straight to the response
        # because MP4 needs the moov atom, which non-fragmented output writes
        # at the end after seeking the file.
        stem = filename.rsplit(".", 1)[0]
        suffix = f"_clip_{int(round(start))}-{int(round(end))}.mp4"
        out_fd, out_path_str = tempfile.mkstemp(prefix=f"{stem}", suffix=suffix)
        os.close(out_fd)
        out_path = Path(out_path_str)

        args = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-nostdin",
            "-y",
            "-ss", f"{start:.3f}",
            "-to", f"{end:.3f}",
            "-i", str(target),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
            str(out_path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            out_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="ffmpeg not available")

        _, err = await proc.communicate()
        if proc.returncode != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
            out_path.unlink(missing_ok=True)
            tail = err.decode(errors="replace").strip().splitlines()[-1:]
            detail = tail[0] if tail else "ffmpeg failed"
            raise HTTPException(status_code=500, detail=f"clip failed: {detail}")

        download_name = f"{stem}{suffix}"

        def _cleanup() -> None:
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("failed to delete clip tempfile %s", out_path)

        return FileResponse(
            out_path,
            media_type="video/mp4",
            filename=download_name,
            background=BackgroundTask(_cleanup),
        )

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

    @app.put("/api/config/idle-retention", response_model=Config)
    async def set_idle_retention(body: IdleRetentionUpdate) -> Config:
        if body.idle_retention_days < 1 or body.idle_retention_days > 3650:
            raise HTTPException(
                status_code=400,
                detail="idle_retention_days must be between 1 and 3650",
            )
        return await manager.set_idle_retention(body.idle_retention_days)

    @app.put("/api/config/motion-threshold", response_model=Config)
    async def set_motion_threshold(body: MotionThresholdUpdate) -> Config:
        if not (0.5 <= body.motion_threshold <= 50.0):
            raise HTTPException(
                status_code=400,
                detail="motion_threshold must be between 0.5 and 50",
            )
        return await manager.set_motion_threshold(body.motion_threshold)

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
