"""Unit checks for Settings.upload_port and app/serve.py's listener construction, plus a
regression test for the dual-listener lifespan bug: app/serve.py runs two uvicorn.Server
instances (ingress + upload) against the SAME FastAPI `app` object via asyncio.gather. Each
Server independently drives the ASGI lifespan protocol unless told not to, so without a
fix, `app`'s lifespan() (DB connection, scan loop, MQTT client — all stored on the shared
app.state) runs TWICE, and the second startup's assignments silently leak the first's
DB connection/scan task/MQTT client. This test actually starts both listeners (on free
ephemeral ports) via app.serve's real dual-server code path and counts how many times the
app's lifespan context is entered, asserting it happens exactly once.
"""
import asyncio
import contextlib
import os
import socket
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def run():
    os.environ.pop("TUV_UPLOAD_PORT", None)
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    assert s.upload_port == 8101, s.upload_port

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

    failures = _run_dual_listener_lifespan_check()

    print()
    print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
    if failures:
        raise SystemExit(1)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            try:
                sock.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.05)
    return False


def _run_dual_listener_lifespan_check() -> list[str]:
    """Regression test for the shared-app double-lifespan bug. Actually starts both
    uvicorn listeners via app.serve._run() and counts app.router.lifespan_context entries
    — this is the exact hook Starlette's Router uses to drive lifespan, regardless of
    whether it's triggered by uvicorn's own ASGI lifespan handling (the buggy path, twice)
    or by an explicit single `async with app.router.lifespan_context(app):` wrapper (the
    fixed path, once)."""
    failures: list[str] = []

    def check(name: str, cond: bool, extra: str = "") -> None:
        print(("PASS" if cond else "FAIL"), name, extra)
        if not cond:
            failures.append(name)

    work = tempfile.mkdtemp(prefix="tuv-serve-test-")
    data_dir = os.path.join(work, "data")
    os.makedirs(data_dir, exist_ok=True)

    ingress_port = _free_port()
    upload_port = _free_port()

    saved_env = {
        k: os.environ.get(k)
        for k in ("TUV_DATA_DIR", "TUV_CACHE_DIR", "TUV_TESLACAM_PATH",
                   "TUV_MQTT_ENABLED", "TUV_PORT", "TUV_UPLOAD_PORT")
    }
    os.environ["TUV_DATA_DIR"] = data_dir
    os.environ["TUV_CACHE_DIR"] = os.path.join(data_dir, "cache")
    os.environ["TUV_TESLACAM_PATH"] = os.path.join(work, "teslacam-nonexistent")
    os.environ["TUV_MQTT_ENABLED"] = "false"
    os.environ["TUV_PORT"] = str(ingress_port)
    os.environ["TUV_UPLOAD_PORT"] = str(upload_port)

    from app.config import get_settings
    get_settings.cache_clear()

    import app.main as main_mod
    import app.serve as serve_mod

    call_count = 0
    real_lifespan_context = main_mod.app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def counting_lifespan(app):
        nonlocal call_count
        call_count += 1
        async with real_lifespan_context(app) as state:
            yield state

    main_mod.app.router.lifespan_context = counting_lifespan

    async def _drive():
        task = asyncio.ensure_future(serve_mod._run())
        try:
            ok_ingress = await asyncio.to_thread(_wait_for_port, ingress_port, 5.0)
            ok_upload = await asyncio.to_thread(_wait_for_port, upload_port, 5.0)
            # Give lifespan startup a moment to fully settle even after the sockets
            # are already accepting connections.
            await asyncio.sleep(0.5)
            return ok_ingress, ok_upload
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    try:
        ok_ingress, ok_upload = asyncio.run(_drive())
    finally:
        main_mod.app.router.lifespan_context = real_lifespan_context
        get_settings.cache_clear()
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        get_settings.cache_clear()

    check("ingress listener came up", ok_ingress)
    check("upload listener came up", ok_upload)
    check("app lifespan entered exactly once across both listeners",
          call_count == 1, f"got {call_count}")

    return failures


if __name__ == "__main__":
    run()
