"""Compute the statistics surfaced to Home Assistant (and the in-UI stats endpoint)."""

from __future__ import annotations

import shutil
from datetime import datetime

from .config import Settings
from .db import Database


async def compute(settings: Settings, db: Database) -> dict:
    base = await _from_db(db)
    disk = _disk_usage(settings) if settings.has_backend() else None
    base["backend_used_bytes"] = (disk or {}).get("used")
    base["backend_free_bytes"] = (disk or {}).get("free")
    base["backend_total_bytes"] = (disk or {}).get("total")
    return base


def _disk_usage(settings: Settings) -> dict | None:
    try:
        usage = shutil.disk_usage(settings.teslacam_path)
    except OSError:
        return None
    return {"total": usage.total, "used": usage.used, "free": usage.free}


async def _from_db(db: Database) -> dict:
    import asyncio

    s = await asyncio.to_thread(db.stats)
    per_folder = s["per_folder"]
    last_refresh = await asyncio.to_thread(db.get_meta, "last_index_refresh")

    saved = per_folder.get("SavedClips", 0)
    sentry = per_folder.get("SentryClips", 0)
    recent = per_folder.get("RecentClips", 0)

    return {
        "total_events": saved + sentry + recent,
        "savedclips_count": saved,
        "sentryclips_count": sentry,
        "recentclips_count": recent,
        "total_video_files": s["total_files"],
        "last_event": _to_iso(s["last_event_ts"]),
        "today_sentry_count": await asyncio.to_thread(_today_sentry, db),
        "last_index_refresh": last_refresh,
    }


def _today_sentry(db: Database) -> int:
    """Sentry events whose local-time date is today (TZ comes from the container env)."""
    today = datetime.now().date().isoformat()
    rows, _ = db.list_events(
        folder="SentryClips", date_from=today, date_to=None, limit=10_000, offset=0
    )
    return len(rows)


def _to_iso(event_ts: str | None) -> str | None:
    """Event timestamps are naive local ISO strings; HA timestamp sensors accept them."""
    return event_ts
