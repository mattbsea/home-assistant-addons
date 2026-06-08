"""End-to-end API test against the fake rclone backend.

Run with:  ./tests/run.sh   (sets up a uv venv, fake rclone, sample backend)

Exercises the whole Phase 1 slice: incremental indexing, event.json parsing, thumbnails,
the prepare->ready cache state machine, HTTP Range/206 video serving, date filtering, stats
(incl. rclone `about`), and ingress base-path injection.
"""
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as m

EVENT_ID = "SavedClips/2024-01-15_10-30-22"
MINUTE = "2024-01-15_10-30-22"
failures = []


def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra)
    if not cond:
        failures.append(name)


def run():
    with TestClient(m.app) as c:
        r = c.post("/api/refresh")
        check("refresh 200", r.status_code == 200, str(r.json()))
        check("refresh added>=1", r.json().get("added", 0) >= 1)

        h = c.get("/api/health").json()
        check("health backend_configured", h["backend_configured"] is True, str(h))

        data = c.get("/api/events?folder=SavedClips").json()
        check("events listed", data["total"] >= 1, str(data["total"]))
        ev = data["events"][0]
        check("reason parsed", ev["reason"] == "user_interaction_honk", str(ev["reason"]))
        check("city parsed", ev["city"] == "Seattle", str(ev["city"]))
        check("file_count==2", ev["file_count"] == 2, str(ev["file_count"]))
        check("thumb_present", ev["thumb_present"] is True)

        d = c.get(f"/api/events/{EVENT_ID}/detail").json()
        check("cameras ordered", d["cameras"] == ["front", "back"], str(d["cameras"]))
        check("one minute", len(d["minutes"]) == 1, str(len(d["minutes"])))

        r = c.get(f"/api/events/{EVENT_ID}/thumb")
        check("thumb served", r.status_code == 200 and r.content == b"PNGDATA")

        r = c.get(f"/api/events/{EVENT_ID}/video/front/{MINUTE}")
        check("video 200 streamed", r.status_code == 200, str(r.status_code))
        check("video full length 4096", len(r.content) == 4096, str(len(r.content)))

        r = c.get(f"/api/events/{EVENT_ID}/video/front/{MINUTE}", headers={"Range": "bytes=0-1023"})
        check("video 206 on range", r.status_code == 206, str(r.status_code))
        check("content-range header", r.headers.get("content-range", "").startswith("bytes 0-1023/4096"))
        check("range body 1024 bytes", len(r.content) == 1024, str(len(r.content)))

        # Legacy rows with an empty path fall back to event_id/filename and still stream.
        import sqlite3 as _sql
        from app.config import get_settings as _gs
        _db = _sql.connect(_gs().db_path)
        _db.execute("UPDATE files SET path='' WHERE event_id=? AND camera='front'", (EVENT_ID,))
        _db.commit(); _db.close()
        r = c.get(f"/api/events/{EVENT_ID}/video/front/{MINUTE}")
        check("legacy empty-path still streams", r.status_code == 200, str(r.status_code))

        r = c.get("/api/events?folder=SavedClips&date_from=2024-01-15T00:00:00&date_to=2024-01-15T23:59:59")
        check("date filter matches day", r.json()["total"] == 1, str(r.json()["total"]))
        r = c.get("/api/events?folder=SavedClips&date_from=2024-02-01T00:00:00&date_to=2024-02-01T23:59:59")
        check("date filter excludes other day", r.json()["total"] == 0, str(r.json()["total"]))

        # RecentClips clips live under a date sub-folder; they must still index and play.
        rec = c.get("/api/events?folder=RecentClips").json()
        check("recent listed", rec["total"] >= 1, str(rec["total"]))
        rec_id = "RecentClips/2024-01-15_10-31-00"
        rd = c.get(f"/api/events/{rec_id}/detail").json()
        check("recent cameras", rd.get("cameras") == ["front", "back"], str(rd.get("cameras")))
        r = c.get(f"/api/events/{rec_id}/video/front/2024-01-15_10-31-00")
        check("recent video 200 (streamed from date subfolder)", r.status_code == 200, str(r.status_code))
        check("recent video length 2048", len(r.content) == 2048, str(len(r.content)))

        # RecentClips has no Tesla thumb.png, so the thumbnailer should have generated one
        # (front-camera frame via ffmpeg) during the /api/refresh above — served from cache.
        check("recent thumb_present false", rec["events"][0]["thumb_present"] is False,
              str(rec["events"][0]["thumb_present"]))
        rt = c.get(f"/api/events/{rec_id}/thumb")
        check("recent generated thumb 200", rt.status_code == 200, str(rt.status_code))
        check("recent thumb is PNG", rt.content[:4] == b"\x89PNG", str(rt.content[:8]))

        s = c.get("/api/stats").json()
        check("stats savedclips_count", s["savedclips_count"] >= 1, str(s.get("savedclips_count")))
        check("stats backend bytes from about", s["backend_used_bytes"] == 400, str(s.get("backend_used_bytes")))

        r = c.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/TOKEN"})
        check("ingress base injected", 'window.INGRESS_BASE = "/api/hassio_ingress/TOKEN"' in r.text)
        check("assets use base", "/api/hassio_ingress/TOKEN/static/app.js" in r.text)

        # Version is injected into the branding and matches the add-on version.
        import app as _app
        check("version injected into shell", f"v{_app.__version__}" in r.text, _app.__version__)
        check("no unrendered version placeholder", "{{VERSION}}" not in r.text)
        cfg = (Path(__file__).resolve().parent.parent / "config.yaml").read_text()
        cfg_ver = next(l.split('"')[1] for l in cfg.splitlines() if l.startswith("version:"))
        check("__version__ matches config.yaml", _app.__version__ == cfg_ver,
              f"{_app.__version__} vs {cfg_ver}")

        # Auto-play regression guard: every camera tile must be muted. Tesla clips have no
        # audio, and an UNmuted <video> gates play() behind a user gesture — which silently
        # breaks gesture-free auto-play-on-open. Muted playback is the universally
        # autoplay-allowed case. (No JS runtime in this suite, so guard the source.)
        pjs = c.get("/static/player.js").text
        check("player mutes all tiles", "v.muted = true" in pjs)
        check("no unmuted master (autoplay gate)", "cam !== master" not in pjs)

        # Metadata overlay (v0.3.0): present in the player, reuses reasonLabel, and the
        # detail contract it depends on returns coordinates.
        check("metadata overlay present", "meta-overlay" in pjs and "meta-clock" in pjs)
        check("overlay toggle present", "meta-toggle" in pjs)
        bjs = c.get("/static/browser.js").text
        check("reasonLabel exposed for overlay", "TUV.reasonLabel = reasonLabel" in bjs)
        check("detail exposes coordinates",
              d.get("est_lat") == 47.6 and d.get("est_lon") == -122.3,
              f"{d.get('est_lat')},{d.get('est_lon')}")

        # Default browse view is the "All" folder (no folder pre-selected).
        idx = c.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/TOKEN"}).text
        check("default tab is All", 'data-folder="" class="active"' in idx)

        # Malicious X-Ingress-Path must be rejected (no script/quote injection).
        r = c.get("/", headers={"X-Ingress-Path": '/x"></script><script>alert(1)</script>'})
        check("malicious ingress header neutralised",
              "alert(1)" not in r.text and 'window.INGRESS_BASE = ""' in r.text)

    # MQTT publisher must construct on paho 2.x and survive a missing broker.
    from dataclasses import replace
    from app.config import get_settings
    from app.mqtt_publisher import MqttPublisher

    s = replace(get_settings(), mqtt_enabled=True, mqtt_host="127.0.0.1", mqtt_port=1)
    pub = MqttPublisher(s)
    try:
        pub.start()                      # dead broker — must not raise
        pub.publish_states({"total_events": 1})  # not connected — must be a no-op
        check("mqtt start survives dead broker", pub.connected is False)
    except Exception as e:               # noqa: BLE001
        check("mqtt start survives dead broker", False, repr(e))
    finally:
        pub.stop()

    print()
    print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
