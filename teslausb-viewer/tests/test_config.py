"""Unit checks for Settings — local teslacam_path replaces the rclone remote fields."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def run():
    work = tempfile.mkdtemp()
    try:
        teslacam = os.path.join(work, "teslacam")
        os.environ["TUV_DATA_DIR"] = work
        os.environ["TUV_TESLACAM_PATH"] = teslacam

        from app.config import get_settings

        get_settings.cache_clear()
        s = get_settings()
        assert s.teslacam_path == Path(teslacam), s.teslacam_path
        assert s.has_backend() is False, "nonexistent dir must report no backend"

        os.makedirs(teslacam, exist_ok=True)
        get_settings.cache_clear()
        s2 = get_settings()
        assert s2.has_backend() is True

        assert not hasattr(s2, "remote_name"), "remote_name must be removed"
        assert not hasattr(s2, "rclone_conf"), "rclone_conf must be removed"

        print("PASS config teslacam_path + has_backend")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    run()
