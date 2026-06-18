"""Phase 3 — send-config payload helpers (the proxy orchestration is verified at cutover)."""
import importlib

sendconfig = importlib.import_module("app.control.sendconfig")
fields = importlib.import_module("fields")

LEAF = "-----BEGIN CERTIFICATE-----\nLEAF\n-----END CERTIFICATE-----"
INT1 = "-----BEGIN CERTIFICATE-----\nINT1\n-----END CERTIFICATE-----"
INT2 = "-----BEGIN CERTIFICATE-----\nINT2\n-----END CERTIFICATE-----"


def test_extract_ca_chain_drops_leaf():
    chain = sendconfig.extract_ca_chain("\n".join([LEAF, INT1, INT2]))
    assert "LEAF" not in chain
    assert "INT1" in chain and "INT2" in chain


def test_extract_ca_chain_single_cert_is_empty():
    assert sendconfig.extract_ca_chain(LEAF) == ""


def test_build_request_shape_uses_field_roster():
    req = sendconfig.build_request("doodle.mbarclay.org", 4443, "CA", ["VIN1", "VIN2"])
    assert req["vins"] == ["VIN1", "VIN2"]
    cfg = req["config"]
    assert cfg["hostname"] == "doodle.mbarclay.org" and cfg["port"] == 4443 and cfg["ca"] == "CA"
    assert cfg["fields"] is fields.TELEMETRY_FIELDS
    assert "NetworkInterface" not in cfg["fields"]      # the v0.10.14 fix stays


def test_send_validates_inputs():
    assert sendconfig.send(vins=[], client_id="c", refresh_token="r", domain="d", region="na",
                           cert_file="/tmp/x", private_key_file="/tmp/k")["ok"] is False
    assert sendconfig.send(vins=["V"], client_id="", refresh_token="r", domain="d", region="na",
                           cert_file="/tmp/x", private_key_file="/tmp/k")["ok"] is False
