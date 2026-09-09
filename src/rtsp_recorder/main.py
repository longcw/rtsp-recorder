"""FastAPI app + REST API for managing the recorder service."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import time
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

from . import audio_index, audio_peaks, idle_index
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

# How long a finished clip export waits to be downloaded before it is swept.
_CLIP_KEEP_SECONDS = 15 * 60

# Bars in the per-recording waveform thumbnails. Enough to read at thumbnail
# width, small enough that one response can carry every file in a stream.
_LIST_WAVEFORM_BUCKETS = 48


def _clip_status(job: dict) -> dict:
    return {
        "state": job["state"],
        "progress": job["progress"],
        "error": job["error"],
        "download_name": job["download_name"],
    }


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

    # Clip exports in flight or waiting to be downloaded, keyed by job id.
    # Their files live under clips_dir, which is wiped on startup because a
    # job cannot outlive the process that tracks it.
    clips_dir = data_dir / "clips"
    clip_jobs: dict[str, dict] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        shutil.rmtree(clips_dir, ignore_errors=True)
        clips_dir.mkdir(parents=True, exist_ok=True)
        await store.load()
        await manager.start()
        try:
            yield
        finally:
            for job in clip_jobs.values():
                if job["proc"] is not None and job["proc"].returncode is None:
                    job["proc"].kill()
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
            if f.name in (idle_index.INDEX_FILENAME, audio_index.INDEX_FILENAME):
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

    @app.get("/api/streams/{name}/waveforms", response_model=dict)
    async def list_waveforms(name: str) -> dict:
        """Thumbnail-resolution waveform for every analyzed file in a stream.

        Kept off `/files` on purpose: that response carries every recording and
        the UI re-polls it every few seconds, while these change only when the
        analyzer finishes a new segment.
        """
        target = _resolve_stream_dir(name)
        data = await asyncio.to_thread(audio_index.load, target)
        out: dict[str, str | None] = {}
        for filename, entry in data.items():
            peaks = entry.get("peaks") if isinstance(entry, dict) else None
            out[filename] = (
                audio_peaks.downsample(peaks, _LIST_WAVEFORM_BUCKETS)
                if isinstance(peaks, str)
                else None
            )
        return out

    @app.get("/api/streams/{name}/files/{filename}/waveform", response_model=dict)
    async def file_waveform(name: str, filename: str) -> dict:
        if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
            raise HTTPException(status_code=400, detail="invalid filename")
        target = _resolve_stream_dir(name)
        entry = (await asyncio.to_thread(audio_index.load, target)).get(filename)
        if not isinstance(entry, dict):
            # Not looked at yet — the analyzer skips the live segment and
            # anything still settling, so the UI should ask again later.
            return {"peaks": None, "duration": None, "pending": True}
        peaks = entry.get("peaks")
        duration = entry.get("duration")
        return {
            "peaks": peaks if isinstance(peaks, str) else None,
            "duration": float(duration) if isinstance(duration, (int, float)) else None,
            "pending": False,
        }

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

            # Open up-front so an FD-exhaustion error surfaces as a clean 503
            # before any response headers are sent — otherwise the OSError
            # gets raised mid-stream and Starlette's exception group propagates
            # an unhandled traceback through the ASGI stack.
            try:
                f = open(target, "rb")
            except OSError as e:
                logger.warning("open failed for %s: %s", target, e)
                raise HTTPException(status_code=503, detail="server busy") from e
            try:
                f.seek(start)
            except OSError:
                f.close()
                raise

            def iter_range():
                # OS page cache handles the hot path. Chunking caps memory
                # and lets uvicorn flush progressively while the client
                # buffers ahead.
                try:
                    remaining = length
                    while remaining > 0:
                        data = f.read(min(_RANGE_CHUNK, remaining))
                        if not data:
                            break
                        remaining -= len(data)
                        yield data
                finally:
                    f.close()

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

        try:
            return FileResponse(
                target,
                media_type="video/mp4",
                filename=filename,
                headers={"Accept-Ranges": "bytes"},
            )
        except OSError as e:
            logger.warning("open failed for %s: %s", target, e)
            raise HTTPException(status_code=503, detail="server busy") from e

    @app.post("/api/streams/{name}/files/{filename}/clip", response_model=dict)
    async def start_clip(
        name: str,
        filename: str,
        start: float = Query(..., ge=0.0),
        end: float = Query(..., gt=0.0),
        speed: float = Query(1.0, ge=1.0, le=64.0),
    ) -> dict:
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

        # An export is a job, not a request: ffmpeg runs in the background
        # while the client polls for progress, then fetches the finished file.
        # Holding one HTTP request open for a multi-minute re-encode would run
        # into any reverse proxy's read timeout and give the user no feedback.
        # Finished jobs nobody collected are swept on the next start.
        now = time.monotonic()
        for job_id, job in list(clip_jobs.items()):
            if job["state"] != "running" and now - job["finished_at"] > _CLIP_KEEP_SECONDS:
                job["path"].unlink(missing_ok=True)
                clip_jobs.pop(job_id, None)

        stem = filename.rsplit(".", 1)[0]
        speed_tag = "" if speed <= 1.0 else f"_{speed:g}x"
        download_name = (
            f"{stem}_clip_{int(round(start))}-{int(round(end))}{speed_tag}.mp4"
        )
        job_id = secrets.token_urlsafe(8)
        path = clips_dir / f"{job_id}.mp4"
        out_duration = (end - start) / speed
        base = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-nostats", "-progress", "pipe:1",
        ]
        trim = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(target)]
        out = ["-movflags", "+faststart", str(path)]
        if speed <= 1.0:
            attempts = [
                [*base, *trim, "-c", "copy", "-avoid_negative_ts", "make_zero", *out]
            ]
        else:
            # Speeding up needs new timestamps, which means a re-encode. Drop
            # audio (useless at Nx) and cap the output at 30fps so high speeds
            # decimate frames instead of encoding every source frame. Every
            # source frame still has to be decoded, and that dominates, so
            # try NVDEC + NVENC first and fall back to the CPU when there is
            # no usable card.
            speedup = ["-vf", f"setpts=PTS/{speed:g},fps=30", "-an"]
            attempts = [
                [
                    *base, "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
                    *trim, *speedup,
                    "-c:v", "h264_nvenc", "-preset", "p4",
                    "-rc", "vbr", "-cq", "27", "-b:v", "0",
                    *out,
                ],
                [
                    *base, *trim, *speedup,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-pix_fmt", "yuv420p",
                    *out,
                ],
            ]

        job: dict = {
            "state": "running",
            "progress": 0.0,
            "error": None,
            "download_name": download_name,
            "path": path,
            "proc": None,
            "finished_at": 0.0,
        }

        async def run() -> None:
            try:
                for i, args in enumerate(attempts):
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            *args,
                            stdin=asyncio.subprocess.DEVNULL,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                    except FileNotFoundError:
                        job.update(state="error", error="ffmpeg not available")
                        return
                    job["proc"] = proc
                    err_task = asyncio.create_task(proc.stderr.read())
                    # -progress prints key=value blocks; out_time_us is how much
                    # of the output exists so far, and it stays N/A until the
                    # first frame lands. Anything that fails at setup (no GPU,
                    # bad codec) exits before that, which is when a fallback
                    # is still safe.
                    produced = False
                    async for raw in proc.stdout:
                        key, _, value = raw.decode(errors="replace").strip().partition("=")
                        if key == "out_time_us" and value.isdigit():
                            produced = True
                            job["progress"] = min(1.0, int(value) / 1_000_000 / out_duration)
                    await proc.wait()
                    if job["state"] == "cancelled":
                        return
                    if proc.returncode == 0:
                        job.update(state="done", progress=1.0)
                        return
                    tail = (await err_task).decode(errors="replace").strip().splitlines()[-1:]
                    detail = tail[0] if tail else "ffmpeg failed"
                    if not produced and i + 1 < len(attempts):
                        logger.info("clip %s: GPU path unavailable (%s), using CPU", download_name, detail)
                        continue
                    logger.warning("clip %s: %s", download_name, detail)
                    job.update(state="error", error=f"clip failed: {detail}")
                    return
            finally:
                job["finished_at"] = time.monotonic()
                if job["state"] != "done":
                    path.unlink(missing_ok=True)

        job["task"] = asyncio.create_task(run())
        clip_jobs[job_id] = job
        return {"id": job_id, **_clip_status(job)}

    @app.get("/api/clips/{job_id}", response_model=dict)
    async def clip_status(job_id: str) -> dict:
        job = clip_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such export")
        return {"id": job_id, **_clip_status(job)}

    @app.delete("/api/clips/{job_id}", response_model=dict)
    async def cancel_clip(job_id: str) -> dict:
        job = clip_jobs.pop(job_id, None)
        if job is None:
            raise HTTPException(status_code=404, detail="no such export")
        if job["state"] == "running":
            job["state"] = "cancelled"
            if job["proc"] is not None and job["proc"].returncode is None:
                job["proc"].kill()
        else:
            job["path"].unlink(missing_ok=True)
        return {"ok": True}

    @app.get("/api/clips/{job_id}/download")
    async def download_clip(job_id: str):
        job = clip_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such export")
        if job["state"] != "done":
            raise HTTPException(status_code=409, detail=f"export is {job['state']}")

        def _cleanup() -> None:
            job["path"].unlink(missing_ok=True)
            clip_jobs.pop(job_id, None)

        return FileResponse(
            job["path"],
            media_type="video/mp4",
            filename=job["download_name"],
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
