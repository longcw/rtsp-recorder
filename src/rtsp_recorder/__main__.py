"""CLI entrypoint: `rtsp-recorder` boots the FastAPI app under uvicorn."""
from __future__ import annotations

import logging
import os

import uvicorn


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("RTSP_RECORDER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
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
