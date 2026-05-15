"""CLI entrypoint: `rtsp-recorder` boots the FastAPI app under uvicorn."""
from __future__ import annotations

import logging
import os
import resource

import uvicorn


_FD_SOFT_TARGET = 65536


def _raise_fd_limit() -> None:
    # macOS launchd hands processes a soft RLIMIT_NOFILE of 256, which a burst
    # of parallel HTTP Range requests against a large recording can easily
    # exhaust on top of the ffmpeg subprocesses we already keep open. Push the
    # soft limit up to the hard cap (clamped — Darwin reports "unlimited" but
    # the kernel actually enforces kern.maxfilesperproc).
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ValueError, OSError):
        return
    target_hard = hard if hard != resource.RLIM_INFINITY else _FD_SOFT_TARGET
    target = min(_FD_SOFT_TARGET, target_hard)
    if soft >= target:
        return
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ValueError, OSError) as e:
        logging.getLogger(__name__).warning(
            "could not raise RLIMIT_NOFILE from %d to %d: %s", soft, target, e
        )


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("RTSP_RECORDER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    _raise_fd_limit()
    # 8765 by default — port 8000 is squatted by many dev tools (Django,
    # Cursor, etc.) which on macOS coexists with our 0.0.0.0 bind via
    # SO_REUSEADDR and silently steals localhost traffic. Override with
    # RTSP_RECORDER_PORT if you need a different value.
    host = os.environ.get("RTSP_RECORDER_HOST", "0.0.0.0")
    port = int(os.environ.get("RTSP_RECORDER_PORT", "8765"))
    uvicorn.run(
        "rtsp_recorder.main:app",
        host=host,
        port=port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
