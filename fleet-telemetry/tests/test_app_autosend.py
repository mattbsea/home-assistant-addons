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


# --- unknown-field handling: state, hash, periodic retry -------------------------------------------------

import json

DAY = autosend.RETRY_SECS
T0 = 1_800_000_000.0
REAL_ERR = 'fleet_telemetry_config failed HTTP 400: {"response":null,"error":"Unknown field AccessoryRail"}'


def _partial(dropped, **extra):
    """A sendconfig.send stub that 'succeeds' after Tesla rejected `dropped`; records what it was offered."""
    seen = []

    def send(**kw):
        seen.append(kw)
        sent = sorted(n for n in kw["roster"] if n not in dropped)
        return {"ok": True, "response": {"updated": 1}, "dropped": [n for n in dropped if n in kw["roster"]],
                "sent_fields": sent, "new_refresh_token": "NEW", **extra}
    send.seen = seen
    return send


def _state(p):
    return tokens.read_state(p["shim_state_path"])


def test_partial_send_is_remembered_and_not_repeated(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    send = _partial(["AccessoryRail", "PowerTransferStatus"])
    monkeypatch.setattr(sendconfig, "send", send)
    logs = []
    assert autosend.maybe_resend(vins=[VIN], now=T0, version="1.0.39", log=logs.append, **p) == "sent"
    st = _state(p)
    assert st["telemetry_fields_hash"] == fields.telemetry_fields_hash()     # hash of the DESIRED roster
    assert set(st["telemetry_unsupported"]) == {"AccessoryRail", "PowerTransferStatus"}
    assert st["telemetry_unsupported"]["AccessoryRail"] == {"first_seen": T0, "last_tried": T0}
    assert st["telemetry_last_send"]["ok"] is True and st["telemetry_last_send"]["dropped"]
    assert any("AccessoryRail" in m and "not supported by Tesla" in m for m in logs)
    # "normal boot" afterwards (same version, any time before the retry is due): nothing is re-pushed
    monkeypatch.setattr(sendconfig, "send", _boom)
    for later in (T0 + 5, T0 + DAY - 1):
        assert autosend.maybe_resend(vins=[VIN], now=later, version="1.0.39", **p) is None


def test_dropped_fields_are_retried_after_a_day_and_turn_on_when_accepted(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    monkeypatch.setattr(sendconfig, "send", _partial(["AccessoryRail"]))
    autosend.maybe_resend(vins=[VIN], now=T0, version="v", **p)
    assert autosend.retry_due(_state(p), fields.effective_roster(), version="v", now=T0 + DAY - 1) is False
    # a day later the FULL roster is offered again; Tesla now accepts everything
    send = _partial([])
    monkeypatch.setattr(sendconfig, "send", send)
    assert autosend.maybe_resend(vins=[VIN], now=T0 + DAY, version="v", **p) == "sent"
    assert "AccessoryRail" in send.seen[0]["roster"]
    st = _state(p)
    assert st["telemetry_unsupported"] == {} and st["telemetry_last_send"]["dropped"] == []
    assert autosend.config_status(p["shim_state_path"], version="v", now=T0 + DAY)["unsupported"] == []


def test_retry_that_is_still_rejected_updates_last_tried_and_keeps_first_seen(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    monkeypatch.setattr(sendconfig, "send", _partial(["AccessoryRail"]))
    autosend.maybe_resend(vins=[VIN], now=T0, version="v", **p)
    send = _partial(["AccessoryRail"])
    monkeypatch.setattr(sendconfig, "send", send)
    assert autosend.maybe_resend(vins=[VIN], now=T0 + DAY, version="v", **p) == "sent"
    assert "AccessoryRail" in send.seen[0]["roster"]                    # offered again
    assert _state(p)["telemetry_unsupported"]["AccessoryRail"] == {"first_seen": T0, "last_tried": T0 + DAY}
    monkeypatch.setattr(sendconfig, "send", _boom)                       # and the day clock restarted
    assert autosend.maybe_resend(vins=[VIN], now=T0 + DAY + 60, version="v", **p) is None


def test_addon_update_retries_dropped_fields_immediately(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    monkeypatch.setattr(sendconfig, "send", _partial(["AccessoryRail"]))
    autosend.maybe_resend(vins=[VIN], now=T0, version="1.0.39", **p)
    send = _partial([])
    monkeypatch.setattr(sendconfig, "send", send)
    assert autosend.maybe_resend(vins=[VIN], now=T0 + 60, version="1.0.40", **p) == "sent"
    assert "AccessoryRail" in send.seen[0]["roster"] and _state(p)["telemetry_unsupported"] == {}


def test_roster_edit_resends_without_the_known_unsupported_fields(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    monkeypatch.setattr(sendconfig, "send", _partial(["AccessoryRail"]))
    autosend.maybe_resend(vins=[VIN], now=T0, version="v", **p)
    tokens.write_state(p["shim_state_path"], telemetry_roster={"Odometer": {"enabled": False}})
    send = _partial([])
    monkeypatch.setattr(sendconfig, "send", send)
    assert autosend.maybe_resend(vins=[VIN], now=T0 + 60, version="v", **p) == "sent"   # hash changed
    offered = send.seen[0]["roster"]
    assert "AccessoryRail" not in offered and "Odometer" not in offered and "Soc" in offered
    assert set(_state(p)["telemetry_unsupported"]) == {"AccessoryRail"}                  # carried over


def test_disabled_unsupported_field_stops_being_reported(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    monkeypatch.setattr(sendconfig, "send", _partial(["AccessoryRail"]))
    autosend.maybe_resend(vins=[VIN], now=T0, version="v", **p)
    tokens.write_state(p["shim_state_path"], telemetry_roster={"AccessoryRail": {"enabled": False}})
    assert autosend.config_status(p["shim_state_path"], version="v", now=T0)["unsupported"] == []


def test_failed_send_with_partial_drops_records_fields_but_not_the_hash(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    monkeypatch.setattr(sendconfig, "send", lambda **kw: {"ok": False, "dropped": ["AccessoryRail"],
                                                          "error": "fleet_telemetry_config failed HTTP 503: x"})
    assert autosend.maybe_resend(vins=[VIN], now=T0, version="v", **p) == "failed"
    st = _state(p)
    assert "telemetry_fields_hash" not in st                            # will retry
    assert set(st["telemetry_unsupported"]) == {"AccessoryRail"}
    assert st["telemetry_last_send"]["ok"] is False and "503" in st["telemetry_last_send"]["error"]


def test_failure_without_drops_records_no_unsupported_state(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    monkeypatch.setattr(sendconfig, "send", lambda **kw: {"ok": False, "dropped": [],
                                                          "error": "fleet_telemetry_config failed HTTP 401: x"})
    assert autosend.maybe_resend(vins=[VIN], now=T0, version="v", **p) == "failed"
    st = _state(p)
    assert "telemetry_unsupported" not in st and "telemetry_fields_hash" not in st


def test_failed_send_keeps_earlier_unsupported_records(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    monkeypatch.setattr(sendconfig, "send", _partial(["AccessoryRail", "PowerTransferStatus"]))
    autosend.maybe_resend(vins=[VIN], now=T0, version="v", **p)
    monkeypatch.setattr(sendconfig, "send", lambda **kw: {"ok": False, "dropped": ["AccessoryRail"], "error": "boom"})
    assert autosend.maybe_resend(vins=[VIN], now=T0 + DAY, version="v", **p) == "failed"
    st = _state(p)
    assert set(st["telemetry_unsupported"]) == {"AccessoryRail", "PowerTransferStatus"}
    assert st["telemetry_unsupported"]["PowerTransferStatus"]["last_tried"] == T0
    assert st["telemetry_unsupported_version"] == "v"


def test_legacy_state_without_unsupported_keys_sends_once_then_is_quiet(tmp_path, monkeypatch):
    """Upgrade from 1.0.38: a stale hash and no telemetry_unsupported -> one send, then nothing."""
    p = _paths(tmp_path)
    tokens.write_state(p["shim_state_path"], telemetry_fields_hash="stale-hash-from-1.0.38")
    monkeypatch.setattr(sendconfig, "send", _partial([]))
    assert autosend.maybe_resend(vins=[VIN], now=T0, version="v", **p) == "sent"
    monkeypatch.setattr(sendconfig, "send", _boom)
    assert autosend.maybe_resend(vins=[VIN], now=T0 + 10 * DAY, version="w", **p) is None


def test_failed_send_is_retried_on_every_later_call(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    n = []
    monkeypatch.setattr(sendconfig, "send", lambda **kw: n.append(1) or {"ok": False, "dropped": [], "error": REAL_ERR})
    for i in range(3):
        assert autosend.maybe_resend(vins=[VIN], now=T0 + i, version="v", **p) == "failed"
    assert len(n) == 3


def test_concurrent_call_is_skipped(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    monkeypatch.setattr(sendconfig, "send", _boom)
    assert autosend._LOCK.acquire(blocking=False)
    try:
        assert autosend.maybe_resend(vins=[VIN], **p) is None
    finally:
        autosend._LOCK.release()


def test_config_status_shape(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    assert autosend.config_status(p["shim_state_path"], now=T0) == {
        "unsupported": [], "note": "", "next_retry_epoch": None, "last_send": None}
    monkeypatch.setattr(sendconfig, "send", _partial(["PowerTransferStatus", "AccessoryRail"]))
    autosend.maybe_resend(vins=[VIN], now=T0, version="v", **p)
    s = autosend.config_status(p["shim_state_path"], version="v", now=T0)
    assert [u["name"] for u in s["unsupported"]] == ["AccessoryRail", "PowerTransferStatus"]   # sorted
    assert "2026.38.6" in s["note"] and s["next_retry_epoch"] == T0 + DAY
    assert s["last_send"]["ok"] is True
