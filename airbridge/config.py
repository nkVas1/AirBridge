"""Configuration management for AirBridge server."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 8090
_DEFAULT_CHUNK_SIZE = 64 * 1024  # 64 KB
_DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB
_DEFAULT_DOWNLOADS_DIR = "AirBridge_Downloads"


@dataclass(frozen=True)
class Config:
    """Immutable server configuration."""

    host: str = "0.0.0.0"
    port: int = _DEFAULT_PORT
    chunk_size: int = _DEFAULT_CHUNK_SIZE
    max_file_size: int = _DEFAULT_MAX_FILE_SIZE
    downloads_dir: Path = field(default_factory=lambda: _resolve_downloads_dir())
    service_name: str = "AirBridge"
    mdns_type: str = "_airbridge._tcp.local."
    pin_length: int = 6
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)


def _resolve_downloads_dir() -> Path:
    """Determine the downloads directory, preferring user's Downloads folder."""
    user_downloads = Path.home() / "Downloads" / _DEFAULT_DOWNLOADS_DIR
    return Path(os.environ.get("AIRBRIDGE_DOWNLOADS", str(user_downloads)))


def load_config() -> Config:
    """Load configuration from environment variables with sensible defaults."""
    return Config(
        host=os.environ.get("AIRBRIDGE_HOST", "0.0.0.0"),
        port=int(os.environ.get("AIRBRIDGE_PORT", str(_DEFAULT_PORT))),
        chunk_size=int(os.environ.get("AIRBRIDGE_CHUNK_SIZE", str(_DEFAULT_CHUNK_SIZE))),
        max_file_size=int(os.environ.get("AIRBRIDGE_MAX_FILE_SIZE", str(_DEFAULT_MAX_FILE_SIZE))),
        log_level=os.environ.get("AIRBRIDGE_LOG_LEVEL", "INFO"),
    )
