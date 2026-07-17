"""End-to-end UI/API checks not already covered by test_indexer.py, test_video_thumb.py,
test_auth.py, or test_upload.py: event listing/filtering, ingress base-path injection (incl.
the XSS guard), static asset content, version injection, and the MQTT publisher smoke test.

Run with:  ./tests/run.sh
"""
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as m

EVENT_ID = "SavedClips/2024-01-15_10-30-22"
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
        check("detail exposes coordinates",
              d.get("est_lat") == 47.6 and d.get("est_lon") == -122.3,
              f"{d.get('est_lat')},{d.get('est_lon')}")

        r = c.get("/api/events?folder=SavedClips&date_from=2024-01-15T00:00:00&date_to=2024-01-15T23:59:59")
        check("date filter matches day", r.json()["total"] == 1, str(r.json()["total"]))
        r = c.get("/api/events?folder=SavedClips&date_from=2024-02-01T00:00:00&date_to=2024-02-01T23:59:59")
        check("date filter excludes other day", r.json()["total"] == 0, str(r.json()["total"]))

        rec = c.get("/api/events?folder=RecentClips").json()
        check("recent listed", rec["total"] >= 1, str(rec["total"]))
        check("recent thumb_present false", rec["events"][0]["thumb_present"] is False,
              str(rec["events"][0]["thumb_present"]))

        s = c.get("/api/stats").json()
        check("stats savedclips_count", s["savedclips_count"] >= 1, str(s.get("savedclips_count")))
        check("stats backend_total_bytes present", isinstance(s.get("backend_total_bytes"), int),
              str(s.get("backend_total_bytes")))

        r = c.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/TOKEN"})
        check("ingress base injected", 'window.INGRESS_BASE = "/api/hassio_ingress/TOKEN"' in r.text)
        check("assets use base", "/api/hassio_ingress/TOKEN/static/app.js" in r.text)

        import app as _app
        check("version injected into shell", f"v{_app.__version__}" in r.text, _app.__version__)
        check("no unrendered version placeholder", "{{VERSION}}" not in r.text)
        cfg = (Path(__file__).resolve().parent.parent / "config.yaml").read_text()
        cfg_ver = next(l.split('"')[1] for l in cfg.splitlines() if l.startswith("version:"))
        check("__version__ matches config.yaml", _app.__version__ == cfg_ver,
              f"{_app.__version__} vs {cfg_ver}")

        pjs = c.get("/static/player.js").text
        check("player mutes all tiles", "v.muted = true" in pjs)
        check("no unmuted master (autoplay gate)", "cam !== master" not in pjs)
        check("metadata overlay present", "meta-overlay" in pjs and "meta-clock" in pjs)
        check("overlay toggle present", "meta-toggle" in pjs)
        bjs = c.get("/static/browser.js").text
        check("reasonLabel exposed for overlay", "TUV.reasonLabel = reasonLabel" in bjs)

        idx = c.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/TOKEN"}).text
        check("default tab is All", 'data-folder="" class="active"' in idx)

        r = c.get("/", headers={"X-Ingress-Path": '/x"></script><script>alert(1)</script>'})
        check("malicious ingress header neutralised",
              "alert(1)" not in r.text and 'window.INGRESS_BASE = ""' in r.text)

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
