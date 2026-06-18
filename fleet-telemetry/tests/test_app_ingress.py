"""Phase 3 — assembled ingress app (config-aware index + /api/state) and pubkey listener."""
import importlib

from starlette.testclient import TestClient

state = importlib.import_module("app.state")
wizard = importlib.import_module("app.web.wizard")
main = importlib.import_module("app.main")


def _ingress(tmp_path, store):
    return wizard.build_wizard_app(
        config_path=str(tmp_path / "c.json"), private_key_path=str(tmp_path / "p.pem"),
        public_key_path=str(tmp_path / "pub.pem"), cert_file=str(tmp_path / "server.crt"),
        certs_dir=str(tmp_path), registry=None, store=store, version="1.0.0", namespace="tesla_telemetry")


def test_index_switches_wizard_to_dashboard(tmp_path):
    c = TestClient(_ingress(tmp_path, state.Store()))
    unconfigured = c.get("/").text
    c.post("/api/config", json={"npm": {"url": "https://npm", "cert_domain": "d"}, "tesla": {"client_id": "cid"}})
    configured = c.get("/").text
    assert "<!DOCTYPE html>" in unconfigured and "<!DOCTYPE html>" in configured
    assert unconfigured != configured                       # wizard page vs dashboard page


def test_api_state_served_from_ingress(tmp_path):
    store = state.Store()
    store.ingest({"msg": "record_payload", "vin": "V", "data": {"Soc": 50.0}})
    st = TestClient(_ingress(tmp_path, store)).get("/api/state").json()
    assert st["version"] == "1.0.0" and st["vehicles"][0]["vin"] == "V"


def test_pubkey_listener(tmp_path):
    keyfile = tmp_path / "pub.pem"
    c = TestClient(main.build_pubkey_app(str(keyfile)))
    assert c.get(main.PUBKEY_WELL_KNOWN).status_code == 404
    keyfile.write_bytes(b"-----BEGIN PUBLIC KEY-----\nx\n-----END PUBLIC KEY-----\n")
    r = c.get(main.PUBKEY_WELL_KNOWN)
    assert r.status_code == 200 and b"PUBLIC KEY" in r.content
