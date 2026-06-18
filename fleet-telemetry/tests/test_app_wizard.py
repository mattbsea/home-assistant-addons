"""Phase 3 — wizard web app: page + config GET/PATCH + keypair over HTTP."""
import importlib

from starlette.testclient import TestClient

wizard = importlib.import_module("app.web.wizard")
config = importlib.import_module("app.control.config")
tesla = importlib.import_module("app.control.tesla")


def _client(tmp_path):
    return TestClient(wizard.build_wizard_app(
        config_path=str(tmp_path / "wizard-config.json"),
        private_key_path=str(tmp_path / "private-key.pem"),
        public_key_path=str(tmp_path / "public-key.pem"),
    ))


def test_page_served(tmp_path):
    r = _client(tmp_path).get("/")
    assert r.status_code == 200 and "<!DOCTYPE html>" in r.text


def test_config_roundtrip_and_secret_masking(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/config").json()["tesla"]["region"] == "na"     # defaults
    c.post("/api/config", json={"tesla": {"client_id": "abc", "client_secret": "s3cret"}})
    got = c.get("/api/config").json()
    assert got["tesla"]["client_id"] == "abc"
    assert got["tesla"]["client_secret"] == config.SECRET_MASK         # masked on read
    # posting the mask back leaves the stored secret unchanged
    c.post("/api/config", json={"tesla": {"client_secret": config.SECRET_MASK, "region": "eu"}})
    assert c.get("/api/config").json()["tesla"]["region"] == "eu"


def test_keypair_route_generates_and_sets_flag(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/keypair", json={}).json()
    assert r["ok"] and len(r["fingerprint"]) == 32
    assert c.get("/api/config").json()["tesla"]["keypair_generated"] is True


def test_bad_config_patch_rejected(tmp_path):
    assert _client(tmp_path).post("/api/config", json=["not", "an", "object"]).status_code == 400


def test_oauth_url_route(tmp_path):
    c = _client(tmp_path)
    c.post("/api/config", json={"tesla": {"client_id": "CID"}})
    r = c.get("/api/oauth/url", params={"redirect_uri": "https://cb", "state": "s1"}).json()
    assert "client_id=CID" in r["url"] and "state=s1" in r["url"]


def test_oauth_exchange_persists_token(tmp_path, monkeypatch):
    c = _client(tmp_path)
    c.post("/api/config", json={"tesla": {"client_id": "c", "client_secret": "s"}})
    monkeypatch.setattr(tesla, "exchange_code", lambda **kw: {"ok": True, "refresh_token": "RT123"})
    assert c.post("/api/oauth/exchange", json={"code": "abc", "redirect_uri": "https://cb"}).json()["ok"]
    assert c.get("/api/config").json()["tesla"]["shim_refresh_token"] == config.SECRET_MASK  # persisted


def test_partner_persists_flag(tmp_path, monkeypatch):
    c = _client(tmp_path)
    c.post("/api/config", json={"tesla": {"client_id": "c", "client_secret": "s", "pubkey_domain": "d.org"}})
    monkeypatch.setattr(tesla, "register_partner", lambda **kw: {"ok": True})
    assert c.post("/api/partner").json()["ok"]
    assert c.get("/api/config").json()["tesla"]["partner_registered"] is True
