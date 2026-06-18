"""Phase 3 — wizard web app: page + config GET/PATCH + keypair over HTTP."""
import importlib

from starlette.testclient import TestClient

wizard = importlib.import_module("app.web.wizard")
config = importlib.import_module("app.control.config")


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
