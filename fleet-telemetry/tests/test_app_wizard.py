"""Wizard/ingress app — exact v0 endpoint contract (/setup, /api/wizard/*, / redirect)."""
import importlib

from starlette.testclient import TestClient

state = importlib.import_module("app.state")
wizard = importlib.import_module("app.web.wizard")
config = importlib.import_module("app.control.config")
tesla = importlib.import_module("app.control.tesla")


def _client(tmp_path, store=None):
    return TestClient(wizard.build_wizard_app(
        config_path=str(tmp_path / "c.json"), wizard_state_path=str(tmp_path / "ws.json"),
        private_key_path=str(tmp_path / "priv.pem"), public_key_path=str(tmp_path / "pub.pem"),
        cert_file=str(tmp_path / "server.crt"), certs_dir=str(tmp_path),
        store=store or state.Store(), registry=None, version="1.0.0"))


def test_setup_page(tmp_path):
    r = _client(tmp_path).get("/setup")
    assert r.status_code == 200 and "<!DOCTYPE html>" in r.text


def test_index_redirects_to_setup_until_completed(tmp_path):
    c = _client(tmp_path)
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and "setup" in r.headers["location"]
    c.post("/api/wizard/save", json={"completed": True})
    r2 = c.get("/", follow_redirects=False)
    assert r2.status_code == 200 and "<!DOCTYPE html>" in r2.text


def test_config_roundtrip_and_masking(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/wizard/config").json()["tesla"]["region"] == "na"
    body = c.post("/api/wizard/config", json={"tesla": {"client_id": "abc", "client_secret": "s3cret"}}).json()
    assert body["ok"] and body["config"]["tesla"]["client_secret"] == config.SECRET_MASK
    assert c.get("/api/wizard/config").json()["tesla"]["client_id"] == "abc"


def test_wizard_state_save_and_read(tmp_path):
    c = _client(tmp_path)
    c.post("/api/wizard/save", json={"step": 3, "inputs": {"x": 1}})
    assert c.get("/api/wizard/state").json()["step"] == 3


def test_keypair(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/wizard/keypair", json={}).json()
    assert r["ok"] and len(r["fingerprint"]) == 32
    assert c.get("/api/wizard/config").json()["tesla"]["keypair_generated"] is True


def test_oauth_url_post(tmp_path):
    c = _client(tmp_path)
    c.post("/api/wizard/config", json={"tesla": {"client_id": "CID"}})
    r = c.post("/api/wizard/oauth-url", json={"redirect_uri": "https://cb", "state": "s1"}).json()
    assert "client_id=CID" in r["url"] and "state=s1" in r["url"]
    assert c.post("/api/wizard/oauth-url", json={}).status_code == 400


def test_oauth_exchange_persists_token(tmp_path, monkeypatch):
    c = _client(tmp_path)
    c.post("/api/wizard/config", json={"tesla": {"client_id": "c", "client_secret": "s"}})
    monkeypatch.setattr(tesla, "exchange_code", lambda **kw: {"ok": True, "refresh_token": "RT"})
    assert c.post("/api/wizard/oauth-exchange", json={"code": "x", "redirect_uri": "r"}).json()["ok"]
    assert c.get("/api/wizard/config").json()["tesla"]["shim_refresh_token"] == config.SECRET_MASK


def test_hostports(tmp_path):
    h = _client(tmp_path).get("/api/wizard/hostports").json()
    assert "telemetry_host_port" in h and "pubkey_host_port" in h


def test_check_records_and_unknown(tmp_path):
    c = _client(tmp_path)
    assert c.post("/api/wizard/check", json={"check": "records"}).json()["ok"] is False  # nothing yet
    assert c.post("/api/wizard/check", json={"check": "bogus"}).status_code == 400
