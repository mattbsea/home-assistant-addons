"""Auto re-send of fleet_telemetry_config when the requested-field roster changes."""
import importlib

autosend = importlib.import_module("app.control.autosend")
sendconfig = importlib.import_module("app.control.sendconfig")
tokens = importlib.import_module("app.control.tokens")
cfgmod = importlib.import_module("app.control.config")
import fields

VIN = "7SAYGDEE3PF884783"


def _paths(tmp_path, completed=True):
    p = {"config_path": str(tmp_path / "c.json"), "shim_state_path": str(tmp_path / "shim.json"),
         "wizard_state_path": str(tmp_path / "ws.json"), "cert_file": str(tmp_path / "server.crt"),
         "private_key_path": str(tmp_path / "priv.pem")}
    cfgmod.save(p["config_path"], {"tesla": {"client_id": "cid", "telemetry_domain": "d.org",
                                             "region": "na", "shim_refresh_token": "SEED"}})
    cfgmod.save_wizard_state(p["wizard_state_path"], {"completed": completed})
    open(p["cert_file"], "w").write("x")
    open(p["private_key_path"], "w").write("x")
    return p


def _boom(**kw):
    raise AssertionError("sendconfig.send should not have been called")


def test_resend_fires_when_roster_changes_then_is_idempotent(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    sent = {}
    monkeypatch.setattr(sendconfig, "send",
                        lambda **kw: (sent.update(kw) or {"ok": True, "response": {"updated": 1},
                                                          "new_refresh_token": "NEW"}))
    assert autosend.maybe_resend(vins=[VIN], **p) == "sent"
    assert sent["vins"] == [VIN] and sent["domain"] == "d.org" and sent["region"] == "na"
    st = tokens.read_state(p["shim_state_path"])
    assert st["refresh_token"] == "NEW"                                  # rotated token persisted
    assert st["telemetry_fields_hash"] == fields.telemetry_fields_hash()  # marked sent
    # second pass: hash now matches -> no send
    monkeypatch.setattr(sendconfig, "send", _boom)
    assert autosend.maybe_resend(vins=[VIN], **p) is None


def test_no_resend_until_setup_complete(tmp_path, monkeypatch):
    p = _paths(tmp_path, completed=False)
    monkeypatch.setattr(sendconfig, "send", _boom)
    assert autosend.maybe_resend(vins=[VIN], **p) is None


def test_no_resend_without_vins(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    monkeypatch.setattr(sendconfig, "send", _boom)
    assert autosend.maybe_resend(vins=[], **p) is None


def test_failed_resend_persists_token_but_not_hash(tmp_path, monkeypatch):
    """A rejected send is inert on the car; locally we keep the rotated token but DON'T record the
    hash, so the next prime cycle retries."""
    p = _paths(tmp_path)
    monkeypatch.setattr(sendconfig, "send",
                        lambda **kw: {"ok": False, "error": "Unknown field Bogus", "new_refresh_token": "ROT"})
    assert autosend.maybe_resend(vins=[VIN], **p) == "failed"
    st = tokens.read_state(p["shim_state_path"])
    assert st["refresh_token"] == "ROT"                 # persisted even though the send failed
    assert "telemetry_fields_hash" not in st            # not marked sent -> will retry
