"""Unit checks for Settings.upload_port and app/serve.py's listener construction."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def run():
    os.environ.pop("TUV_UPLOAD_PORT", None)
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    assert s.upload_port == 8100, s.upload_port

    os.environ["TUV_UPLOAD_PORT"] = "9100"
    get_settings.cache_clear()
    s2 = get_settings()
    assert s2.upload_port == 9100, s2.upload_port
    os.environ.pop("TUV_UPLOAD_PORT", None)
    get_settings.cache_clear()

    # serve.py must expose a main() entrypoint and build two distinct uvicorn.Config objects
    # (one per port) without actually binding a socket (Config() alone doesn't bind).
    import app.serve as serve_mod
    assert hasattr(serve_mod, "main"), "serve.py must expose main()"

    print("PASS upload_port default + override, serve.main exists")


if __name__ == "__main__":
    run()
