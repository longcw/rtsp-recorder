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
    host = os.environ.get("RTSP_RECORDER_HOST", "0.0.0.0")
    port = int(os.environ.get("RTSP_RECORDER_PORT", "8000"))
    uvicorn.run(
        "rtsp_recorder.main:app",
        host=host,
        port=port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
