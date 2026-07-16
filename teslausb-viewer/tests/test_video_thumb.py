"""End-to-end check: local thumbnail + Range video serving (no rclone, no upload yet)."""
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _write_sample_tree(root):
    ev = os.path.join(root, "SavedClips", "2024-01-15_10-30-22")
    os.makedirs(ev, exist_ok=True)
    with open(os.path.join(ev, "thumb.png"), "wb") as f:
        f.write(b"PNGDATA")
    with open(os.path.join(ev, "2024-01-15_10-30-22-front.mp4"), "wb") as f:
        f.write(os.urandom(4096))


def run():
    work = tempfile.mkdtemp()
    try:
        teslacam = os.path.join(work, "teslacam")
        os.makedirs(teslacam)
        _write_sample_tree(teslacam)

        os.environ["TUV_TESLACAM_PATH"] = teslacam
        os.environ["TUV_DATA_DIR"] = work
        os.environ["TUV_CACHE_DIR"] = os.path.join(work, "cache")
        os.environ["TUV_MQTT_ENABLED"] = "false"

        from app.config import get_settings
        get_settings.cache_clear()

        from fastapi.testclient import TestClient
        import app.main as m

        failures = []

        def check(name, cond, extra=""):
            print(("PASS" if cond else "FAIL"), name, extra)
            if not cond:
                failures.append(name)

        with TestClient(m.app) as c:
            r = c.post("/api/refresh")
            check("refresh 200", r.status_code == 200, str(r.json()))

            r = c.get("/api/events/SavedClips/2024-01-15_10-30-22/thumb")
            check("thumb served", r.status_code == 200 and r.content == b"PNGDATA")

            r = c.get("/api/events/SavedClips/2024-01-15_10-30-22/video/front/2024-01-15_10-30-22")
            check("video 200", r.status_code == 200, str(r.status_code))
            check("video full length 4096", len(r.content) == 4096, str(len(r.content)))

            r = c.get(
                "/api/events/SavedClips/2024-01-15_10-30-22/video/front/2024-01-15_10-30-22",
                headers={"Range": "bytes=0-1023"},
            )
            check("video 206 on range", r.status_code == 206, str(r.status_code))
            check("content-range header",
                  r.headers.get("content-range", "").startswith("bytes 0-1023/4096"))
            check("range body 1024 bytes", len(r.content) == 1024, str(len(r.content)))

            # Legacy rows with an empty path fall back to event_id/filename and still stream.
            _db = sqlite3.connect(get_settings().db_path)
            _db.execute(
                "UPDATE files SET path='' WHERE event_id=? AND camera='front'",
                ("SavedClips/2024-01-15_10-30-22",),
            )
            _db.commit()
            _db.close()
            r = c.get("/api/events/SavedClips/2024-01-15_10-30-22/video/front/2024-01-15_10-30-22")
            check("legacy empty-path still streams", r.status_code == 200, str(r.status_code))

        print()
        print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
        if failures:
            raise SystemExit(1)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    run()
