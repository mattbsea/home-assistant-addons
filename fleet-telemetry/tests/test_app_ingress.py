"""Ingress: /api/state served from the Store (with cert), and the pubkey listener."""
import importlib

from starlette.testclient import TestClient

state = importlib.import_module("app.state")
wizard = importlib.import_module("app.web.wizard")
main = importlib.import_module("app.main")


def _client(tmp_path, store):
    return TestClient(wizard.build_wizard_app(
        config_path=str(tmp_path / "c.json"), wizard_state_path=str(tmp_path / "ws.json"),
        private_key_path=str(tmp_path / "p.pem"), public_key_path=str(tmp_path / "pub.pem"),
        cert_file=str(tmp_path / "server.crt"), certs_dir=str(tmp_path),
        store=store, registry=None, version="1.0.0"))


def test_api_state_served_from_store(tmp_path):
    store = state.Store()
    store.ingest({"msg": "record_payload", "vin": "V", "data": {"Soc": 50.0}})
    st = _client(tmp_path, store).get("/api/state").json()
    assert st["version"] == "1.0.0"
    assert st["vehicles"][0]["vin"] == "V"
    assert "cert" in st                      # cert_detail (ok:False here — no cert file)


def test_pubkey_listener(tmp_path):
    keyfile = tmp_path / "pub.pem"
    c = TestClient(main.build_pubkey_app(str(keyfile)))
    assert c.get(main.PUBKEY_WELL_KNOWN).status_code == 404
    keyfile.write_bytes(b"-----BEGIN PUBLIC KEY-----\nx\n-----END PUBLIC KEY-----\n")
    r = c.get(main.PUBKEY_WELL_KNOWN)
    assert r.status_code == 200 and b"PUBLIC KEY" in r.content
