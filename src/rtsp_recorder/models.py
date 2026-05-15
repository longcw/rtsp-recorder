"""Pydantic models shared by config, manager and API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

StreamState = Literal[
    "stopped",
    "starting",
    "recording",
    "reconnecting",
    "error",
]


class Stream(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    url: str = Field(..., min_length=1)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _safe_name(cls, v: str) -> str:
        # Restrict to a safe set so the name can be used as a folder name.
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        if not v or any(c not in allowed for c in v):
            raise ValueError(
                "name must be 1-64 chars of letters, digits, underscore or hyphen"
            )
        return v


class Config(BaseModel):
    streams: list[Stream] = Field(default_factory=list)
    retention_days: int = Field(default=7, ge=1, le=3650)
    # How long each rotating file is, in seconds. Default 5 minutes; bounded to
    # keep ffmpeg's segment muxer healthy and the file count reasonable.
    segment_seconds: int = Field(default=300, ge=10, le=3600)
    # IANA timezone name. Used as the TZ env var for ffmpeg so that filenames
    # (which use strftime) and any time-of-day logic are interpreted in this
    # zone rather than the server's. Defaults to UTC.
    timezone: str = Field(default="UTC")
    # Whether the recorder daemon is running. Persisted so the desired state
    # survives restarts.
    running: bool = True

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError, OSError) as e:
            raise ValueError(f"unknown timezone {v!r}: {e}") from e
        return v


class StreamStatus(BaseModel):
    name: str
    url: str
    enabled: bool
    state: StreamState
    started_at: datetime | None = None
    last_error: str | None = None
    restart_count: int = 0
    current_file: str | None = None


class ServiceStatus(BaseModel):
    running: bool
    retention_days: int
    segment_seconds: int
    timezone: str
    streams: list[StreamStatus]


class RecordingFile(BaseModel):
    name: str
    size: int
    modified_at: datetime
    # Wall-clock time when this segment began, parsed from the filename
    # (which ffmpeg generates with strftime in the configured timezone).
    # Naive on purpose: the filename carries no zone, and we want the UI
    # to display it as-is rather than convert it to the browser's locale.
    # None if the filename doesn't match the YYYY-MM-DD_HH-MM-SS pattern.
    started_at: datetime | None = None
    # Approximate length in seconds: mtime - (parsed start, localized to
    # the configured tz). None when started_at could not be parsed.
    duration_seconds: float | None = None
