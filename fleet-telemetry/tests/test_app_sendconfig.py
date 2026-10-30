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


# --- unknown-field fallback -------------------------------------------------------------------------

import json

import pytest

REAL_400 = '{"response":null,"error":"Unknown field AccessoryRail","error_description":"","txid":"abc"}'


def _roster(*names):
    return {n: {"interval_seconds": 60} for n in names}


def _fake_post(unknown, calls):
    """A `post` that answers HTTP 400 "Unknown field X" for any name in `unknown` still in the payload."""
    def post(payload):
        sent = json.loads(payload)["config"]["fields"]
        calls.append(sorted(sent))
        for n in unknown:
            if n in sent:
                return 400, json.dumps({"response": None, "error": f"Unknown field {n}"})
        return 200, '{"response":{"updated_vehicles":1}}'
    return post


def _run(post, roster, **kw):
    return sendconfig.post_with_fallback(post, vins=["V"], domain="d.org", port=4443, ca_chain="CA",
                                         roster=roster, **kw)


@pytest.mark.parametrize("status,body,expected", [
    (400, REAL_400, "AccessoryRail"),                                      # the real API answer
    (400, '{"error":"unknown field: Foo2"}', "Foo2"),                      # case / colon variation
    (400, '{"error":"Unknown field \\"Bar\\""}', "Bar"),                   # quoted name
    (400, "Unknown field Baz", "Baz"),                                     # not JSON
    (400, '{"error":"Unknown field Evil; rm -rf"}', "Evil"),               # letters/digits only
    (400, '{"error":"Invalid value for field Soc"}', None),                # a different 400
    (400, '{"error":"unauthorized"}', None),
    (401, REAL_400, None), (403, REAL_400, None), (500, REAL_400, None),   # other statuses never match
    (400, "", None), (400, None, None),
])
def test_parse_unknown_field(status, body, expected):
    assert sendconfig.parse_unknown_field(status, body) == expected


def test_one_unknown_field_is_dropped_and_the_send_succeeds():
    calls, logs = [], []
    r = _run(_fake_post({"AccessoryRail"}, calls), _roster("Soc", "AccessoryRail", "Odometer"), log=logs.append)
    assert r["ok"] is True and r["dropped"] == ["AccessoryRail"]
    assert r["sent_fields"] == ["Odometer", "Soc"]
    assert calls == [["AccessoryRail", "Odometer", "Soc"], ["Odometer", "Soc"]]   # second POST lacks it
    assert len(logs) == 1 and "AccessoryRail" in logs[0] and "1/10" in logs[0]


def test_nothing_unknown_is_a_single_post():
    calls = []
    r = _run(_fake_post(set(), calls), _roster("Soc", "Odometer"))
    assert r["ok"] and r["dropped"] == [] and len(calls) == 1


def test_several_unknown_fields_are_dropped_in_sequence():
    calls, logs = [], []
    unknown = ["AccessoryRail", "PowerTransferStatus", "RemoteStartActive"]
    r = _run(_fake_post(set(unknown), calls), _roster("Soc", "Odometer", *unknown), log=logs.append)
    assert r["ok"] and sorted(r["dropped"]) == sorted(unknown)
    assert r["sent_fields"] == ["Odometer", "Soc"] and len(calls) == 4 and len(logs) == 3


def test_drop_cap_gives_up_with_a_clear_error():
    names = [f"Field{i}" for i in range(20)]
    calls = []
    r = _run(_fake_post(set(names), calls), _roster("Soc", *names), max_drops=3)
    assert r["ok"] is False and len(r["dropped"]) == 3 and len(calls) == 4   # 3 drops + the refused 4th POST
    assert "gave up after dropping 3 unknown fields" in r["error"] and "HTTP 400" in r["error"]


def test_default_cap_is_ten():
    names = [f"Field{i}" for i in range(20)]
    r = _run(_fake_post(set(names), []), _roster("Soc", *names))
    assert r["ok"] is False and len(r["dropped"]) == sendconfig.MAX_UNKNOWN_DROPS == 10


@pytest.mark.parametrize("status,body", [
    (400, '{"error":"Invalid interval for field Soc"}'),    # a different 400
    (401, REAL_400), (403, REAL_400), (500, REAL_400), (502, "Bad gateway"),
])
def test_other_errors_drop_nothing(status, body):
    calls = []
    r = _run(lambda payload: (calls.append(1) or (status, body)), _roster("Soc", "AccessoryRail"))
    assert r["ok"] is False and r["dropped"] == [] and len(calls) == 1
    assert f"HTTP {status}" in r["error"]


def test_network_error_drops_nothing_and_keeps_earlier_drops():
    seen = []

    def post(payload):
        seen.append(1)
        if len(seen) == 1:
            return 400, REAL_400
        raise OSError("connection reset")
    r = _run(post, _roster("Soc", "AccessoryRail"))
    assert r["ok"] is False and r["dropped"] == ["AccessoryRail"] and "connection reset" in r["error"]


def test_unknown_name_not_in_roster_is_not_looped_on():
    calls = []
    r = _run(lambda p: (calls.append(1) or (400, '{"error":"Unknown field Ghost"}')), _roster("Soc", "Odometer"))
    assert r["ok"] is False and r["dropped"] == [] and len(calls) == 1


def test_essential_fields_are_never_dropped():
    calls = []
    r = _run(lambda p: (calls.append(1) or (400, '{"error":"Unknown field Location"}')), _roster("Location", "Gear2"))
    assert r["ok"] is False and r["dropped"] == [] and len(calls) == 1


def test_the_last_remaining_field_is_not_dropped():
    r = _run(lambda p: (400, '{"error":"Unknown field Only"}'), _roster("Only"))
    assert r["ok"] is False and r["dropped"] == []


def test_caller_roster_is_not_mutated():
    roster = _roster("Soc", "AccessoryRail")
    _run(_fake_post({"AccessoryRail"}, []), roster)
    assert set(roster) == {"Soc", "AccessoryRail"}
