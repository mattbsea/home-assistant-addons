"""Fleet API call logging wrappers (app/control/fleetlog.py)."""
import json

import pytest

from app import reclog
from app.control import fleetlog


def _log(tmp_path):
    return reclog.RecordLog(str(tmp_path / "fleet.jsonl"))


def _lines(tmp_path):
    return [json.loads(x) for x in (tmp_path / "fleet.jsonl").read_text().splitlines()]


def test_get_is_logged_with_response(tmp_path):
    log = _log(tmp_path)
    get = fleetlog.wrap_get(log, lambda url, token: {"response": [{"vin": "ABC", "state": "online"}]})
    out = get("https://fleet/api/1/products", "tok")
    assert out["response"][0]["vin"] == "ABC"   # passthrough unchanged
    rec = _lines(tmp_path)[-1]
    assert rec["kind"] == "fleet_get"
    assert rec["url"].endswith("/products")
    assert rec["ok"] is True
    assert rec["response"]["response"][0]["state"] == "online"


def test_token_secrets_are_redacted_in_response(tmp_path):
    log = _log(tmp_path)
    post = fleetlog.wrap_post_form(log, lambda url, data: {
        "access_token": "SECRET-AT", "refresh_token": "SECRET-RT", "id_token": "SECRET-ID",
        "expires_in": 28800})
    out = post("https://auth/oauth2/v3/token", {"grant_type": "refresh_token", "client_id": "cid"})
    assert out["access_token"] == "SECRET-AT"    # caller still gets the real token
    rec = _lines(tmp_path)[-1]
    assert rec["response"]["access_token"] == "<redacted>"
    assert rec["response"]["refresh_token"] == "<redacted>"
    assert rec["response"]["id_token"] == "<redacted>"
    assert rec["response"]["expires_in"] == 28800  # non-secret kept
    # Sanity: the real secret string never appears anywhere in the persisted log.
    assert "SECRET-AT" not in (tmp_path / "fleet.jsonl").read_text()


def test_request_secrets_are_redacted(tmp_path):
    log = _log(tmp_path)
    post = fleetlog.wrap_post_form(log, lambda url, data: {"access_token": "x"})
    post("https://auth/oauth2/v3/token",
         {"grant_type": "refresh_token", "client_id": "cid", "refresh_token": "REQ-RT"})
    rec = _lines(tmp_path)[-1]
    assert rec["request"]["refresh_token"] == "<redacted>"
    assert rec["request"]["client_id"] == "cid"
    assert "REQ-RT" not in (tmp_path / "fleet.jsonl").read_text()


def test_errors_are_logged_and_reraised(tmp_path):
    log = _log(tmp_path)

    def boom(url, token):
        raise RuntimeError("HTTP 408")
    get = fleetlog.wrap_get(log, boom)
    with pytest.raises(RuntimeError):
        get("https://fleet/api/1/vehicles/1/vehicle_data", "tok")
    rec = _lines(tmp_path)[-1]
    assert rec["ok"] is False
    assert "408" in rec["error"]


def test_none_log_is_passthrough(tmp_path):
    sentinel = object()
    get = fleetlog.wrap_get(None, lambda url, token: sentinel)
    assert get("u", "t") is sentinel   # no wrapping, no file
