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
    # Shorter retention applied to recordings flagged as "idle" (no detectable
    # motion). Bounded the same way as retention_days. Must be <= retention_days
    # at use sites; we accept any valid value here and clamp in the pruner so
    # the API stays simple.
    idle_retention_days: int = Field(default=1, ge=1, le=3650)
    # Motion-detector threshold, in *percent of frame area* showing
    # structural motion. The detector samples keyframes, spatial-blurs
    # them, subtracts a per-pair median diff (kills uniform brightness
    # drift), then counts pixels whose residual diff exceeds a fixed
    # noise floor. The 97th-percentile fraction across pairs must stay
    # below this value for a clip to be classified idle. Lower = more
    # sensitive (fewer idle); higher = more permissive. A baby's hand
    # typically lights up 0.3–1% of the frame at 160×90, so the default
    # catches subtle limb movement.
    motion_threshold: float = Field(default=1.0, ge=0.5, le=50.0)
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
    idle_retention_days: int
    motion_threshold: float
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
    # True if background-subtraction analysis classified the whole recording
    # as idle (no motion). False if motion was detected, or if the user has
    # explicitly cleared the tag. None when not yet analyzed — usually the
    # currently-recording segment, or any segment finalized in the last
    # analysis-loop tick.
    idle: bool | None = None
    # True while the analyzer is currently chewing on this file. Used by the
    # UI to render a spinner on the row in flight; transient (flips back to
    # False once the result lands in the idle index). The analyzer is
    # sequential, so at most one file across the whole service has this
    # set at any moment.
    analyzing: bool = False
    # Progress fraction in [0,1] for the file currently being analyzed.
    # Null when `analyzing` is false or when the duration probe failed so
    # we couldn't compute a denominator.
    analyze_progress: float | None = None
