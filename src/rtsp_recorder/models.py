"""Pydantic models shared by config, manager and API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

StreamState = Literal["stopped", "starting", "recording", "error"]


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
    # Whether the recorder daemon is running. Persisted so the desired state
    # survives restarts.
    running: bool = True


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
    streams: list[StreamStatus]


class RecordingFile(BaseModel):
    name: str
    size: int
    modified_at: datetime
