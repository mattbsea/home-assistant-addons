"""Multi-select bulk delete: /api/events/delete removes files from disk + drops index rows.

Builds its own isolated fixture tree (own temp dir + env vars), independent of the shared
sample tree tests/run.sh sets up for test_api.py.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _write_sample_tree(root):
    # Two SavedClips events — only one gets deleted, proving the other is untouched.
    keep_ev = os.path.join(root, "SavedClips", "2024-01-16_09-00-00")
    os.makedirs(keep_ev, exist_ok=True)
    with open(os.path.join(keep_ev, "thumb.png"), "wb") as f:
        f.write(b"PNGDATA")
    with open(os.path.join(keep_ev, "2024-01-16_09-00-00-front.mp4"), "wb") as f:
        f.write(os.urandom(1024))

    del_ev = os.path.join(root, "SavedClips", "2024-01-15_10-30-22")
    os.makedirs(del_ev, exist_ok=True)
    with open(os.path.join(del_ev, "thumb.png"), "wb") as f:
        f.write(b"PNGDATA")
    with open(os.path.join(del_ev, "2024-01-15_10-30-22-front.mp4"), "wb") as f:
        f.write(os.urandom(4096))
    with open(os.path.join(del_ev, "2024-01-15_10-30-22-back.mp4"), "wb") as f:
        f.write(os.urandom(4096))

    # Two RecentClips minute-groups sharing one date directory — deleting one must not
    # touch the other's files (this is what forces per-file unlink instead of rmtree).
    rec = os.path.join(root, "RecentClips", "2024-01-15")
    os.makedirs(rec, exist_ok=True)
    with open(os.path.join(rec, "2024-01-15_10-31-00-front.mp4"), "wb") as f:
        f.write(os.urandom(2048))
    with open(os.path.join(rec, "2024-01-15_10-32-00-front.mp4"), "wb") as f:
        f.write(os.urandom(2048))


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
        from app.cache import CacheManager

        failures = []

        def check(name, cond, extra=""):
            print(("PASS" if cond else "FAIL"), name, extra)
            if not cond:
                failures.append(name)

        del_saved = "SavedClips/2024-01-15_10-30-22"
        keep_saved = "SavedClips/2024-01-16_09-00-00"
        del_recent = "RecentClips/2024-01-15_10-31-00"
        keep_recent = "RecentClips/2024-01-15_10-32-00"
        missing_id = "RecentClips/2024-01-15_23-59-00"

        with TestClient(m.app) as c:
            r = c.post("/api/refresh")
            check("refresh 200", r.status_code == 200, str(r.json()))

            # Populate the thumbnail cache for the event we're about to delete, so we can
            # confirm delete also cleans up the cached copy, not just the source file.
            r = c.get(f"/api/events/{del_saved}/thumb")
            check("thumb served before delete", r.status_code == 200 and r.content == b"PNGDATA")
            cache_path = CacheManager(get_settings()).thumb_path(del_saved)
            check("thumb cached on disk before delete", cache_path.is_file())

            r = c.post(
                "/api/events/delete",
                json={"event_ids": [del_saved, del_recent, missing_id]},
            )
            check("delete 200", r.status_code == 200, str(r.status_code))
            body = r.json()
            check("deleted list correct", sorted(body["deleted"]) == sorted([del_saved, del_recent]),
                  str(body["deleted"]))
            check("failed list has missing id", len(body["failed"]) == 1
                  and body["failed"][0]["event_id"] == missing_id, str(body["failed"]))

            # Files actually gone from disk.
            check("saved event dir removed",
                  not os.path.isdir(os.path.join(teslacam, del_saved)))
            check("recent clip removed",
                  not os.path.isfile(os.path.join(teslacam, "RecentClips", "2024-01-15",
                                                   "2024-01-15_10-31-00-front.mp4")))
            check("thumb cache removed", not cache_path.is_file())

            # Untouched siblings still on disk.
            check("kept saved event dir untouched",
                  os.path.isdir(os.path.join(teslacam, keep_saved)))
            check("sibling recent clip untouched",
                  os.path.isfile(os.path.join(teslacam, "RecentClips", "2024-01-15",
                                               "2024-01-15_10-32-00-front.mp4")))

            # Index no longer serves the deleted events, still serves the kept ones.
            r = c.get(f"/api/events/{del_saved}/detail")
            check("deleted saved event 404s", r.status_code == 404, str(r.status_code))
            r = c.get(f"/api/events/{del_recent}/detail")
            check("deleted recent event 404s", r.status_code == 404, str(r.status_code))
            r = c.get(f"/api/events/{keep_saved}/detail")
            check("kept saved event still served", r.status_code == 200, str(r.status_code))
            r = c.get(f"/api/events/{keep_recent}/detail")
            check("kept recent event still served", r.status_code == 200, str(r.status_code))

            # Re-running delete on an already-deleted id is a clean partial failure, not a 500.
            r = c.post("/api/events/delete", json={"event_ids": [del_saved]})
            check("re-delete 200", r.status_code == 200, str(r.status_code))
            check("re-delete reports not found",
                  r.json()["failed"] == [{"event_id": del_saved, "error": "not found"}],
                  str(r.json()))

        print()
        print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
        if failures:
            raise SystemExit(1)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    run()
