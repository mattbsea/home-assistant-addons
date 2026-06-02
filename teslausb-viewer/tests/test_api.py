"""End-to-end API test against the fake rclone backend.

Run with:  ./tests/run.sh   (sets up a uv venv, fake rclone, sample backend)

Exercises the whole Phase 1 slice: incremental indexing, event.json parsing, thumbnails,
the prepare->ready cache state machine, HTTP Range/206 video serving, date filtering, stats
(incl. rclone `about`), and ingress base-path injection.
"""
import os
import time

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
        check("video 425 before prepare", r.status_code == 425, str(r.status_code))

        c.post(f"/api/events/{EVENT_ID}/prepare")
        st = {}
        for _ in range(50):
            st = c.get(f"/api/events/{EVENT_ID}/status").json()
            if st["state"] in ("ready", "error"):
                break
            time.sleep(0.1)
        check("prepare ready", st.get("state") == "ready", str(st))

        r = c.get(f"/api/events/{EVENT_ID}/video/front/{MINUTE}")
        check("video 200 after prepare", r.status_code == 200, str(r.status_code))
        check("video full length 4096", len(r.content) == 4096, str(len(r.content)))

        r = c.get(f"/api/events/{EVENT_ID}/video/front/{MINUTE}", headers={"Range": "bytes=0-1023"})
        check("video 206 on range", r.status_code == 206, str(r.status_code))
        check("content-range header", r.headers.get("content-range", "").startswith("bytes 0-1023/4096"))
        check("range body 1024 bytes", len(r.content) == 1024, str(len(r.content)))

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
        c.post(f"/api/events/{rec_id}/prepare")
        rst = {}
        for _ in range(50):
            rst = c.get(f"/api/events/{rec_id}/status").json()
            if rst["state"] in ("ready", "error"):
                break
            time.sleep(0.1)
        check("recent prepare ready", rst.get("state") == "ready", str(rst))
        r = c.get(f"/api/events/{rec_id}/video/front/2024-01-15_10-31-00")
        check("recent video 200 (fetched from date subfolder)", r.status_code == 200, str(r.status_code))
        check("recent video length 2048", len(r.content) == 2048, str(len(r.content)))

        s = c.get("/api/stats").json()
        check("stats savedclips_count", s["savedclips_count"] >= 1, str(s.get("savedclips_count")))
        check("stats backend bytes from about", s["backend_used_bytes"] == 400, str(s.get("backend_used_bytes")))

        r = c.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/TOKEN"})
        check("ingress base injected", 'window.INGRESS_BASE = "/api/hassio_ingress/TOKEN"' in r.text)
        check("assets use base", "/api/hassio_ingress/TOKEN/static/app.js" in r.text)

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
