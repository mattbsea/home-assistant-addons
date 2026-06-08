"""Unit checks for StreamServer command assembly (no real rclone)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def run():
    from app.config import get_settings
    from app.stream import StreamServer

    s = get_settings()
    cmd = StreamServer(s)._cmd()
    assert cmd[0] == "rclone", cmd
    assert "serve" in cmd and "http" in cmd, cmd
    assert s.remote_base() in cmd, cmd
    assert "--read-only" in cmd, cmd
    assert f"127.0.0.1:{s.stream_port}" in cmd, cmd
    assert f"{s.cache_size_mb}M" in cmd, cmd
    print("PASS stream _cmd assembly")


if __name__ == "__main__":
    run()
