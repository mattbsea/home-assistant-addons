"""Unit checks for the upload-port restriction middleware.

Calls the middleware function directly against hand-built Starlette Requests with different
scope["server"] ports, rather than spinning up real uvicorn sockets (out of scope/flaky for
this suite — see the design spec's Testing section).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_request(app, path, port):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "server": ("testhost", port),
        "client": ("1.2.3.4", 12345),
        "scheme": "http",
        "app": app,
    }
    return Request(scope)


async def _call_next_ok(request):
    from starlette.responses import Response
    return Response("ok", status_code=200)


def run():
    os.environ.setdefault("TUV_TESLACAM_PATH", "/tmp/tuv-port-restriction-nonexistent")
    os.environ.setdefault("TUV_MQTT_ENABLED", "false")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app, restrict_upload_port

    settings = get_settings()
    failures = []

    def check(name, cond, extra=""):
        print(("PASS" if cond else "FAIL"), name, extra)
        if not cond:
            failures.append(name)

    async def _run_checks():
        # Non-upload path on the upload port -> 404, never reaches call_next.
        req = _make_request(app, "/api/events", settings.upload_port)
        resp = None
        status = None
        try:
            resp = await restrict_upload_port(req, _call_next_ok)
            status = resp.status_code
        except Exception as exc:  # HTTPException raised directly is also acceptable
            status = getattr(exc, "status_code", None)
        check("non-upload path on upload port -> 404", status == 404, str(status))

        # Upload path on the upload port -> passes through to call_next (200 from our stub).
        req = _make_request(app, "/api/upload/SavedClips/x/y.mp4", settings.upload_port)
        resp = await restrict_upload_port(req, _call_next_ok)
        check("upload path on upload port -> passes through", resp.status_code == 200,
              str(resp.status_code))

        # Non-upload path on the ingress port (not upload_port) -> passes through.
        req = _make_request(app, "/api/events", settings.port)
        resp = await restrict_upload_port(req, _call_next_ok)
        check("non-upload path on ingress port -> passes through", resp.status_code == 200,
              str(resp.status_code))

    asyncio.run(_run_checks())

    print()
    print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
