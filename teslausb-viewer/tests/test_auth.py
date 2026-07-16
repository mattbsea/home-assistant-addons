"""Unit checks for require_ha_token (mocks the Supervisor core API call)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeAsyncClient:
    def __init__(self, *, status_code, raise_transport_error=False, raise_other_error=False):
        self._status = status_code
        self._raise = raise_transport_error
        self._raise_other = raise_other_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *_a, **_kw):
        import httpx
        if self._raise:
            raise httpx.TransportError("boom")
        if self._raise_other:
            raise RuntimeError("unexpected bug")
        return _FakeResponse(self._status)


def run():
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    import app.auth as auth_mod

    app = FastAPI()

    @app.get("/protected")
    async def protected(_: None = Depends(auth_mod.require_ha_token)):
        return {"ok": True}

    failures = []

    def check(name, cond, extra=""):
        print(("PASS" if cond else "FAIL"), name, extra)
        if not cond:
            failures.append(name)

    with TestClient(app) as c:
        r = c.get("/protected")
        check("no header -> 401", r.status_code == 401, str(r.status_code))

        r = c.get("/protected", headers={"Authorization": "Bearer "})
        check("empty token -> 401", r.status_code == 401, str(r.status_code))

        auth_mod.httpx.AsyncClient = lambda **kw: _FakeAsyncClient(status_code=200)
        r = c.get("/protected", headers={"Authorization": "Bearer good-token"})
        check("valid token -> 200", r.status_code == 200, str(r.status_code))

        auth_mod.httpx.AsyncClient = lambda **kw: _FakeAsyncClient(status_code=401)
        r = c.get("/protected", headers={"Authorization": "Bearer bad-token"})
        check("rejected token -> 401", r.status_code == 401, str(r.status_code))

        auth_mod.httpx.AsyncClient = lambda **kw: _FakeAsyncClient(
            status_code=200, raise_transport_error=True
        )
        r = c.get("/protected", headers={"Authorization": "Bearer whatever"})
        check("supervisor unreachable -> 401", r.status_code == 401, str(r.status_code))

        auth_mod.httpx.AsyncClient = lambda **kw: _FakeAsyncClient(
            status_code=200, raise_other_error=True
        )
        r = c.get("/protected", headers={"Authorization": "Bearer whatever"})
        check(
            "unexpected exception during validation -> 401 (not 500)",
            r.status_code == 401,
            str(r.status_code),
        )

    print()
    print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
