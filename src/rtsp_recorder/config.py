"""Config persistence: JSON file with atomic writes and an async lock."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from .models import Config


class ConfigStore:
    """Reads and writes the JSON config file. Safe to use concurrently."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._config: Config = Config()

    async def load(self) -> Config:
        async with self._lock:
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text())
                    self._config = Config.model_validate(data)
                except (json.JSONDecodeError, ValueError) as e:
                    raise RuntimeError(f"Invalid config at {self.path}: {e}") from e
            else:
                self._config = Config()
                await self._write_locked(self._config)
            return self._config.model_copy(deep=True)

    async def get(self) -> Config:
        async with self._lock:
            return self._config.model_copy(deep=True)

    async def update(self, new_config: Config) -> Config:
        async with self._lock:
            await self._write_locked(new_config)
            self._config = new_config
            return self._config.model_copy(deep=True)

    async def _write_locked(self, config: Config) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: tmp file in same dir, then rename.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".config-", suffix=".json", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(config.model_dump(mode="json"), f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise
