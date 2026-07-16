"""Data models and TeslaCam filename/timestamp parsing helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Top-level TeslaCam folders TeslaUSB archives. Phase 1 indexes SavedClips; the
# others are wired in here so later phases only flip them on in the indexer.
FOLDERS = ("SavedClips", "SentryClips", "RecentClips")

# Camera angles, in the order they should be laid out in the player grid.
CAMERAS = ("front", "back", "left_repeater", "right_repeater", "left_pillar", "right_pillar")

# Event folder name, e.g. 2024-01-15_10-30-22
EVENT_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")

# Per-camera clip, e.g. 2024-01-15_10-30-22-left_repeater.mp4
CLIP_RE = re.compile(
    r"^(?P<minute>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(?P<camera>[a-z_]+)\.mp4$"
)


def parse_event_timestamp(name: str) -> datetime | None:
    """Parse a `YYYY-MM-DD_HH-MM-SS` folder/minute name into a timezone-aware datetime.

    Tesla writes local wall-clock names, so we interpret them in the container's timezone
    (set from Home Assistant by run.sh) and attach that offset. Home Assistant's
    `device_class: timestamp` sensors reject naive datetimes, so the offset is required.
    """
    try:
        return datetime.strptime(name, "%Y-%m-%d_%H-%M-%S").astimezone()
    except ValueError:
        return None


def iso_or_none(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


@dataclass
class CameraFile:
    camera: str
    minute_ts: str  # YYYY-MM-DD_HH-MM-SS of this one-minute clip
    filename: str  # basename, e.g. 2024-01-15_10-30-22-front.mp4
    size: int = 0
    path: str = ""  # local subpath under teslacam_path (handles nested layouts like RecentClips/<date>/)


@dataclass
class Event:
    """A SavedClips/SentryClips event, or a synthetic RecentClips minute group."""

    event_id: str  # "<folder>/<event_dir>"
    folder: str
    event_ts: str  # YYYY-MM-DD_HH-MM-SS
    reason: str | None = None
    city: str | None = None
    est_lat: float | None = None
    est_lon: float | None = None
    thumb_present: bool = False
    files: list[CameraFile] = field(default_factory=list)

    @property
    def minutes(self) -> list[str]:
        """Distinct one-minute timestamps in this event, chronological."""
        return sorted({f.minute_ts for f in self.files})

    def cameras_for_minute(self, minute_ts: str) -> dict[str, str]:
        """camera -> filename for a given minute."""
        return {f.camera: f.filename for f in self.files if f.minute_ts == minute_ts}


def event_id(folder: str, event_dir: str) -> str:
    return f"{folder}/{event_dir}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
