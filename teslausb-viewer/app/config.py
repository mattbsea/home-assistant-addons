"""Runtime settings, loaded from the TUV_* environment variables exported by run.sh.

run.sh is the single source of truth for configuration: it reads the add-on options
via bashio and hands them to the app as environment variables. The app never parses
/data/options.json directly, so it behaves identically under the Supervisor and under a
plain `podman run` that sets the same variables.
"""

from __future__ import annotations

import os
import re
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
    rclone_conf: Path
    cache_dir: Path
    remote_name: str
    remote_path: str
    refresh_minutes: int
    cache_size_mb: int
    port: int
    mqtt_enabled: bool
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "index.db"

    def resolved_remote_name(self) -> str:
        """The configured remote, defaulting to the first [section] in rclone.conf."""
        if self.remote_name:
            return self.remote_name
        try:
            text = self.rclone_conf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
        match = re.search(r"^\[([^\]]+)\]", text, re.MULTILINE)
        return match.group(1) if match else ""

    def remote_base(self) -> str:
        """`remote:path` with a normalised (slash-trimmed) base path."""
        name = self.resolved_remote_name()
        path = self.remote_path.strip("/")
        return f"{name}:{path}"

    def has_backend(self) -> bool:
        return self.rclone_conf.is_file() and bool(self.resolved_remote_name())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(os.environ.get("TUV_DATA_DIR", "/data"))
    return Settings(
        data_dir=data_dir,
        rclone_conf=Path(os.environ.get("TUV_RCLONE_CONF", str(data_dir / "rclone.conf"))),
        cache_dir=Path(os.environ.get("TUV_CACHE_DIR", str(data_dir / "cache"))),
        remote_name=_clean(os.environ.get("TUV_REMOTE_NAME")),
        remote_path=_clean(os.environ.get("TUV_REMOTE_PATH")),
        refresh_minutes=max(5, _int("TUV_REFRESH_MINUTES", 30)),
        cache_size_mb=max(256, _int("TUV_CACHE_SIZE_MB", 2048)),
        port=_int("TUV_PORT", 8099),
        mqtt_enabled=os.environ.get("TUV_MQTT_ENABLED", "false").lower() == "true",
        mqtt_host=_clean(os.environ.get("TUV_MQTT_HOST")),
        mqtt_port=_int("TUV_MQTT_PORT", 1883),
        mqtt_username=_clean(os.environ.get("TUV_MQTT_USERNAME")),
        mqtt_password=_clean(os.environ.get("TUV_MQTT_PASSWORD")),
    )
