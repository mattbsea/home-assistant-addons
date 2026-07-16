"""Unit checks for the local-disk Indexer (no HTTP, no rclone)."""
import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _write_sample_tree(root):
    ev = os.path.join(root, "SavedClips", "2024-01-15_10-30-22")
    os.makedirs(ev, exist_ok=True)
    with open(os.path.join(ev, "event.json"), "w") as f:
        f.write('{"reason":"user_interaction_honk","city":"Seattle","est_lat":47.6,"est_lon":-122.3}')
    with open(os.path.join(ev, "thumb.png"), "wb") as f:
        f.write(b"PNGDATA")
    with open(os.path.join(ev, "2024-01-15_10-30-22-front.mp4"), "wb") as f:
        f.write(os.urandom(4096))
    with open(os.path.join(ev, "2024-01-15_10-30-22-back.mp4"), "wb") as f:
        f.write(os.urandom(4096))

    rec = os.path.join(root, "RecentClips", "2024-01-15")
    os.makedirs(rec, exist_ok=True)
    with open(os.path.join(rec, "2024-01-15_10-31-00-front.mp4"), "wb") as f:
        f.write(os.urandom(2048))
    with open(os.path.join(rec, "2024-01-15_10-31-00-back.mp4"), "wb") as f:
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

        from app.config import get_settings
        get_settings.cache_clear()
        from app.db import Database
        from app.indexer import Indexer

        settings = get_settings()
        db = Database(settings.db_path)
        indexer = Indexer(settings, db)

        result = asyncio.run(indexer.scan())
        assert result["added"] >= 2, result  # SavedClips event + RecentClips minute

        row = db.get_event("SavedClips/2024-01-15_10-30-22")
        assert row is not None
        assert row["reason"] == "user_interaction_honk", row
        assert row["city"] == "Seattle", row
        assert row["thumb_present"] == 1, row
        assert len(row["files"]) == 2, row["files"]
        paths = {f["path"] for f in row["files"]}
        assert paths == {
            "SavedClips/2024-01-15_10-30-22/2024-01-15_10-30-22-front.mp4",
            "SavedClips/2024-01-15_10-30-22/2024-01-15_10-30-22-back.mp4",
        }, paths

        rec_row = db.get_event("RecentClips/2024-01-15_10-31-00")
        assert rec_row is not None
        rec_paths = {f["path"] for f in rec_row["files"]}
        assert rec_paths == {
            "RecentClips/2024-01-15/2024-01-15_10-31-00-front.mp4",
            "RecentClips/2024-01-15/2024-01-15_10-31-00-back.mp4",
        }, rec_paths

        db.close()
        print("PASS local indexer scan")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    run()
