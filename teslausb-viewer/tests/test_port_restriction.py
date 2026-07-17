"""Checks for the upload-port restriction middleware.

Includes both:
  - Direct calls to the middleware function against hand-built Starlette Requests with
    different scope["server"] ports (fast, but cannot detect whether an exception raised
    inside the middleware actually gets converted to a clean HTTP response by the ASGI
    stack — BaseHTTPMiddleware sits outside Starlette's ExceptionMiddleware, so a raised
    HTTPException does NOT become a 404; it propagates to ServerErrorMiddleware as a 500).
  - Genuine end-to-end checks using FastAPI's TestClient, which drives a real request
    through the full ASGI stack (all middleware, real response building) so a
    500-vs-404 bug like that would actually be caught.
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

    # --- Genuine end-to-end checks: real HTTP requests through the full ASGI stack. ---
    # BaseHTTPMiddleware sits outside Starlette's ExceptionMiddleware, so raising
    # HTTPException from inside restrict_upload_port does NOT get converted to a clean
    # 404 by the stack — it propagates to ServerErrorMiddleware as a 500. Only a real
    # request through TestClient (not a direct call to the middleware function) can
    # detect that. TestClient's base_url sets scope["server"] to match the host:port.
    from starlette.testclient import TestClient

    # raise_server_exceptions=False: mirrors what a real client over the network sees
    # (a response, be it 200/404/500), instead of TestClient re-raising the exception
    # in-process — which would hide the exact bug this check exists to catch.
    upload_client = TestClient(app, base_url=f"http://testserver:{settings.upload_port}",
                                raise_server_exceptions=False)
    ingress_client = TestClient(app, base_url=f"http://testserver:{settings.port}",
                                 raise_server_exceptions=False)

    resp = upload_client.get("/api/events")
    check("[e2e] non-upload path on upload port -> real HTTP 404",
          resp.status_code == 404, str(resp.status_code))

    resp = upload_client.get("/api/upload/SavedClips/x/y.mp4")
    check("[e2e] upload path on upload port -> not 404 (passes through)",
          resp.status_code != 404, str(resp.status_code))

    resp = ingress_client.get("/api/events")
    check("[e2e] non-upload path on ingress port -> not 404 (passes through)",
          resp.status_code != 404, str(resp.status_code))

    print()
    print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
