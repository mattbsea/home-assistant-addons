"""Runtime settings, loaded from the TUV_* environment variables exported by run.sh.

run.sh is the single source of truth for configuration: it reads the add-on options
via bashio and hands them to the app as environment variables. The app never parses
/data/options.json directly, so it behaves identically under the Supervisor and under a
plain `podman run` that sets the same variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _clean(value: str | None) -> str:
    """Treat bashio's empty/"null" sentinels as unset."""
    if value is None:
        return ""
    value = value.strip()
    return "" if value == "null" else value


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    teslacam_path: Path
    cache_dir: Path
    refresh_minutes: int
    cache_size_mb: int
    port: int
    upload_port: int
    mqtt_enabled: bool
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "index.db"

    def has_backend(self) -> bool:
        return self.teslacam_path.is_dir()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(os.environ.get("TUV_DATA_DIR", "/data"))
    return Settings(
        data_dir=data_dir,
        teslacam_path=Path(os.environ.get("TUV_TESLACAM_PATH", "/media/USBDisk/teslausb")),
        cache_dir=Path(os.environ.get("TUV_CACHE_DIR", str(data_dir / "cache"))),
        refresh_minutes=max(5, _int("TUV_REFRESH_MINUTES", 30)),
        cache_size_mb=max(256, _int("TUV_CACHE_SIZE_MB", 2048)),
        port=_int("TUV_PORT", 8099),
        upload_port=_int("TUV_UPLOAD_PORT", 8101),
        mqtt_enabled=os.environ.get("TUV_MQTT_ENABLED", "false").lower() == "true",
        mqtt_host=_clean(os.environ.get("TUV_MQTT_HOST")),
        mqtt_port=_int("TUV_MQTT_PORT", 1883),
        mqtt_username=_clean(os.environ.get("TUV_MQTT_USERNAME")),
        mqtt_password=_clean(os.environ.get("TUV_MQTT_PASSWORD")),
    )
