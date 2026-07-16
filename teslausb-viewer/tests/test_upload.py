# teslausb-viewer/tests/test_upload.py
"""End-to-end checks for the upload endpoint. Write-behaviour checks bypass real HA-token
validation via a FastAPI dependency override; the last check exercises the real (mocked)
auth path."""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def run():
    work = tempfile.mkdtemp()
    try:
        teslacam = os.path.join(work, "teslacam")
        os.makedirs(teslacam)

        os.environ["TUV_TESLACAM_PATH"] = teslacam
        os.environ["TUV_DATA_DIR"] = work
        os.environ["TUV_CACHE_DIR"] = os.path.join(work, "cache")
        os.environ["TUV_MQTT_ENABLED"] = "false"
        os.environ["TUV_REFRESH_MINUTES"] = "5"

        from app.config import get_settings
        get_settings.cache_clear()

        from fastapi.testclient import TestClient
        import app.main as m
        from app.auth import require_ha_token
        from app.upload import sweep_orphaned_tmp

        failures = []

        def check(name, cond, extra=""):
            print(("PASS" if cond else "FAIL"), name, extra)
            if not cond:
                failures.append(name)

        m.app.dependency_overrides[require_ha_token] = lambda: None

        with TestClient(m.app) as c:
            r = c.put(
                "/api/upload/SavedClips/2024-01-15_10-30-22/2024-01-15_10-30-22-front.mp4",
                content=b"hello-clip-bytes",
            )
            check("valid upload -> 204", r.status_code == 204, str(r.status_code))
            written = os.path.join(
                teslacam, "SavedClips", "2024-01-15_10-30-22", "2024-01-15_10-30-22-front.mp4"
            )
            check("file written to disk", os.path.isfile(written))
            check("file contents match", open(written, "rb").read() == b"hello-clip-bytes")

            r = c.put(
                "/api/upload/SavedClips/2024-01-15_10-30-22/event.json",
                content=b'{"reason":"user_interaction_honk"}',
            )
            check("sidecar upload -> 204", r.status_code == 204, str(r.status_code))

            before_bad_folder = {str(p.relative_to(teslacam)) for p in Path(teslacam).rglob("*")}
            r = c.put("/api/upload/NotAFolder/2024-01-15_10-30-22/thumb.png", content=b"x")
            check("bad folder -> 400", r.status_code == 400, str(r.status_code))
            after_bad_folder = {str(p.relative_to(teslacam)) for p in Path(teslacam).rglob("*")}
            check("bad folder leaves no filesystem trace", before_bad_folder == after_bad_folder)

            before_bad_event_dir = {str(p.relative_to(teslacam)) for p in Path(teslacam).rglob("*")}
            r = c.put("/api/upload/SavedClips/not-a-date/thumb.png", content=b"x")
            check("bad event_dir -> 400", r.status_code == 400, str(r.status_code))
            after_bad_event_dir = {str(p.relative_to(teslacam)) for p in Path(teslacam).rglob("*")}
            check("bad event_dir leaves no filesystem trace", before_bad_event_dir == after_bad_event_dir)

            before_bad_filename = {str(p.relative_to(teslacam)) for p in Path(teslacam).rglob("*")}
            r = c.put("/api/upload/SavedClips/2024-01-15_10-30-22/not-a-clip.txt", content=b"x")
            check("bad filename -> 400", r.status_code == 400, str(r.status_code))
            after_bad_filename = {str(p.relative_to(teslacam)) for p in Path(teslacam).rglob("*")}
            check("bad filename leaves no filesystem trace", before_bad_filename == after_bad_filename)

            r = c.put(
                "/api/upload/SavedClips/2024-01-15_10-30-22/2024-01-15_10-30-22-front.mp4",
                content=b"replaced",
            )
            check("re-upload -> 204", r.status_code == 204, str(r.status_code))
            check("re-upload overwrote contents", open(written, "rb").read() == b"replaced")
            leftovers = [n for n in os.listdir(os.path.dirname(written)) if ".tmp-" in n]
            check("no leftover tmp files", leftovers == [], str(leftovers))

            stale = os.path.join(os.path.dirname(written), ".stale.tmp-deadbeef")
            open(stale, "wb").close()
            old_time = time.time() - 3600
            os.utime(stale, (old_time, old_time))
            removed = sweep_orphaned_tmp(get_settings().teslacam_path, max_age_seconds=1)
            check("stale tmp swept", removed >= 1 and not os.path.exists(stale), str(removed))

        del m.app.dependency_overrides[require_ha_token]

        # Real auth path (no override): Supervisor isn't reachable in this test env, so the
        # dependency's TransportError branch fires and correctly denies the request.
        with TestClient(m.app) as c:
            r = c.put(
                "/api/upload/SavedClips/2024-01-15_10-30-22/2024-01-15_10-30-22-back.mp4",
                content=b"nope",
            )
            check("unauthenticated (unreachable supervisor) -> 401", r.status_code == 401,
                  str(r.status_code))

        print()
        print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
        if failures:
            raise SystemExit(1)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    run()
