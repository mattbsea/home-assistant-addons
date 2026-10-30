"""Self-driving share "since tracking began": baseline seeding (record log / first live reading),
persistence, the arithmetic and its guards, counter-reset re-anchoring, resends, and the API payload."""
import gzip
import importlib
import json

import pytest

import conftest
import fields

state = importlib.import_module("app.state")
api = importlib.import_module("app.web.api")
sdtrack = importlib.import_module("app.sdtrack")

VIN = "TESTVIN0000000001"
OTHER = "TESTVIN0000000002"
SD, TOT = "SelfDrivingMilesSinceReset", "MilesSinceReset"


def rec(sd, total, at="2026-10-30T04:55:34Z", vin=VIN, resend=False, **extra):
    data = {"CreatedAt": at, "IsResend": resend, **extra}
    if sd is not None:
        data[SD] = sd
    if total is not None:
        data[TOT] = total
    return {"msg": "record_payload", "vin": vin, "data": data}


def line(r):
    return json.dumps(r) + "\n"


def make_store(tmp_path, log=True, **kw):
    persist = sdtrack.BaselineStore(str(tmp_path / "shim-state.json"), str(tmp_path / "log.jsonl") if log else "")
    return state.Store(sd_persist=persist), persist


def since(store, vin=VIN):
    return store.self_driving(vin)["since_tracking"]


# --- arithmetic ------------------------------------------------------------------------------------

def base(sd=100.0, total=200.0):
    return {"sd_miles": sd, "total_miles": total, "at": "2026-10-30T04:55:34Z", "source": "live", "reanchor": None}


def test_share_arithmetic_two_decimals_half_up():
    s = fields.self_driving_since(base(), 109, 224)            # 9 / 24 = 37.5
    assert s["sd_miles_gained"] == 9 and s["total_miles_gained"] == 24 and s["share_pct"] == 37.5
    assert s["reason"] is None
    assert fields.self_driving_since(base(0, 0), 1, 800)["share_pct"] == 0.13     # 0.125 -> half up
    assert fields.self_driving_since(base(0, 0), 2, 3 + 0)["share_pct"] == 66.67
    assert fields.self_driving_since(base(0, 0), 5, 5)["share_pct"] == 100.0


def test_gains_use_exact_decimal_difference():
    s = fields.self_driving_since(base(15982.12890625, 16470.6015625), 15983.12890625, 16473.6015625)
    assert s["sd_miles_gained"] == 1.0 and s["total_miles_gained"] == 3.0 and s["share_pct"] == 33.33


def test_under_one_mile_is_na_not_a_share():
    s = fields.self_driving_since(base(), 100, 200)            # nothing gained yet
    assert s["share_pct"] is None and s["reason"] == "under_1_mile"
    s = fields.self_driving_since(base(), 100.5, 200.9)        # 0.9 of a mile is still under 1
    assert s["share_pct"] is None and s["reason"] == "under_1_mile" and s["total_miles_gained"] == 0.9
    assert fields.self_driving_since(base(), 100, 201)["reason"] is None    # exactly 1 mile is enough


def test_guards_missing_inconsistent_below_and_no_baseline():
    assert fields.self_driving_since(None, 1, 2)["reason"] == "no_baseline"
    assert fields.self_driving_since(base(), None, 300)["reason"] == "missing_counter"
    assert fields.self_driving_since(base(), 150, "<invalid>")["reason"] == "missing_counter"
    s = fields.self_driving_since(base(), 160, 230)             # gained 60 sd of 30 total
    assert s["share_pct"] is None and s["reason"] == "inconsistent"
    for cur in ((99, 300), (150, 199)):                         # a counter under its baseline: never negative
        s = fields.self_driving_since(base(), *cur)
        assert s["share_pct"] is None and s["reason"] == "below_baseline" and s["sd_miles_gained"] is None


# --- baseline from the first live reading ----------------------------------------------------------

def test_first_live_reading_is_the_baseline_when_there_is_no_log(tmp_path, capsys):
    store, persist = make_store(tmp_path, log=False)
    store.ingest(rec(15982.12890625, 16470.6015625))
    s = since(store)
    assert s["baseline"] == {"sd_miles": 15982.12890625, "total_miles": 16470.6015625,
                             "at": "2026-10-30T04:55:34Z", "source": "live"}
    assert s["reason"] == "under_1_mile" and s["share_pct"] is None
    out = capsys.readouterr().out
    assert out.count("self-driving baseline") == 1 and "first live reading" in out and "2026-10-30T04:55:34Z" in out
    assert VIN not in out                                       # only the last 4 characters are logged
    store.ingest(rec(15992.12890625, 16480.6015625, at="2026-10-30T05:10:00Z"))
    assert since(store)["share_pct"] == 100.0                   # 10 of 10 miles
    assert since(store)["baseline"]["at"] == "2026-10-30T04:55:34Z"     # later readings never move it
    store.ingest(rec(15992.12890625, 16490.6015625, at="2026-10-30T05:20:00Z"))
    assert since(store)["share_pct"] == 50.0
    assert "self-driving baseline" not in capsys.readouterr().out       # logged once only


def test_counters_arriving_in_separate_records_pair_up(tmp_path):
    store, _ = make_store(tmp_path, log=False)
    store.ingest(rec(50, None, at="2026-10-30T01:00:00Z"))
    assert since(store)["reason"] == "no_baseline"
    store.ingest(rec(None, 70, at="2026-10-30T01:00:05Z"))
    assert since(store)["baseline"]["total_miles"] == 70 and since(store)["baseline"]["at"] == "2026-10-30T01:00:05Z"


def test_invalid_sentinel_never_seeds_the_baseline(tmp_path):
    store, _ = make_store(tmp_path, log=False)
    store.ingest(rec("<invalid>", "<invalid>"))
    assert since(store)["baseline"] is None


def test_store_without_persistence_still_tracks_in_memory():
    store = state.Store()
    store.ingest(rec(10, 20))
    store.ingest(rec(11, 22))
    assert since(store)["share_pct"] == 50.0


def test_unseen_vin_has_no_baseline():
    assert state.Store().self_driving("NOPE")["since_tracking"]["reason"] == "no_baseline"


# --- baseline seeded from the record log -----------------------------------------------------------

def test_baseline_seeded_from_earliest_matching_log_record(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    log.write_text(
        line(rec(None, None, at="2026-10-01T00:00:00Z", Soc=50))                 # no counters
        + line(rec(30, None, at="2026-10-02T00:00:00Z"))                         # only one counter
        + line(rec(900, 950, at="2026-10-03T00:00:00Z", vin=OTHER))              # another vehicle
        + "this is not json\n"
        + '{"msg":"record_payload","vin":"' + VIN + '","data":{"' + SD + '":1,"' + TOT + '":\n'   # truncated line
        + line(rec(40, 100, at="2026-10-04T00:00:00Z"))                          # the earliest match
        + line(rec(60, 150, at="2026-10-05T00:00:00Z")))
    store, _ = make_store(tmp_path)
    store.ingest(rec(60, 150, at="2026-10-05T00:00:00Z"))
    b = since(store)["baseline"]
    assert b == {"sd_miles": 40.0, "total_miles": 100.0, "at": "2026-10-04T00:00:00Z", "source": "record_log"}
    s = since(store)
    assert s["sd_miles_gained"] == 20 and s["total_miles_gained"] == 50 and s["share_pct"] == 40.0
    out = capsys.readouterr().out
    assert "record log" in out and "2026-10-04T00:00:00Z" in out and out.count("self-driving baseline") == 1


def test_log_scan_reads_rotated_files_oldest_first_and_stops_at_first_match(tmp_path):
    p = tmp_path / "log.jsonl"
    (tmp_path / "log.jsonl.20260901T000000Z.gz").write_bytes(gzip.compress(line(rec(None, None)).encode()))   # no match
    (tmp_path / "log.jsonl.20261001T000000Z.gz").write_bytes(
        gzip.compress((line(rec(10, 20, at="2026-09-20T00:00:00Z")) + line(rec(11, 21, at="2026-09-21T00:00:00Z"))).encode()))
    (tmp_path / "log.jsonl.20261015T000000Z.gz").write_bytes(gzip.compress(line(rec(5, 6, at="2026-10-10T00:00:00Z")).encode()))
    p.write_text(line(rec(99, 99, at="2026-10-20T00:00:00Z")))
    b = sdtrack.scan_log(str(p), VIN)
    assert (b["sd_miles"], b["total_miles"], b["at"]) == (10, 20, "2026-09-20T00:00:00Z")   # earliest in the oldest file that has one


def test_log_scan_skips_corrupt_archive_and_falls_through(tmp_path):
    p = tmp_path / "log.jsonl"
    (tmp_path / "log.jsonl.20260901T000000Z.gz").write_bytes(b"not gzip at all")
    whole = gzip.compress(line(rec(1, 2, at="2026-09-02T00:00:00Z")).encode() * 50)
    (tmp_path / "log.jsonl.20260902T000000Z.gz").write_bytes(whole[: len(whole) // 2])   # truncated archive
    p.write_text(line(rec(7, 8, at="2026-10-01T00:00:00Z")))
    b = sdtrack.scan_log(str(p), VIN)
    assert b is not None and b["total_miles"] in (2, 8)         # a readable record from the truncated archive, else the live file


def test_log_scan_missing_file_empty_path_and_overlong_line(tmp_path):
    assert sdtrack.scan_log(str(tmp_path / "absent.jsonl"), VIN) is None
    assert sdtrack.scan_log("", VIN) is None
    p = tmp_path / "log.jsonl"
    p.write_text("x" * (sdtrack.MAX_LINE * 2 + 5) + "\n" + line(rec(3, 4, at="2026-10-01T00:00:00Z")))
    b = sdtrack.scan_log(str(p), VIN)
    assert b and b["sd_miles"] == 3                              # the huge line is dropped, not buffered whole


def test_log_scan_ignores_resend_records_and_invalid_values(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text(line(rec(1, 2, resend=True, at="2026-10-01T00:00:00Z"))
                 + line(rec("<invalid>", 5, at="2026-10-02T00:00:00Z"))
                 + line(rec(20, 30, at="2026-10-03T00:00:00Z")))
    assert sdtrack.scan_log(str(p), VIN)["at"] == "2026-10-03T00:00:00Z"


def test_no_match_in_log_falls_back_to_first_live_reading(tmp_path):
    (tmp_path / "log.jsonl").write_text(line(rec(None, None, Soc=40)) + line(rec(5, None)))
    store, _ = make_store(tmp_path)
    store.ingest(rec(70, 90, at="2026-10-31T00:00:00Z"))
    b = since(store)["baseline"]
    assert b["source"] == "live" and (b["sd_miles"], b["total_miles"]) == (70, 90)


def test_log_is_scanned_once_per_vehicle(tmp_path, monkeypatch):
    store, persist = make_store(tmp_path)
    calls = []
    monkeypatch.setattr(persist, "scan_log", lambda vin: calls.append(vin))
    store.ingest(rec(None, 5))                                   # one counter only: no scan yet
    store.ingest(rec(7, None))
    store.ingest(rec(8, 9))
    store.ingest(rec(9, 10))
    assert calls == [VIN]


def test_baselines_are_per_vehicle(tmp_path):
    (tmp_path / "log.jsonl").write_text(line(rec(10, 20, vin=VIN)) + line(rec(500, 600, vin=OTHER)))
    store, _ = make_store(tmp_path)
    store.ingest(rec(12, 30, vin=VIN))
    store.ingest(rec(505, 610, vin=OTHER))
    assert since(store, VIN)["baseline"]["sd_miles"] == 10 and since(store, OTHER)["baseline"]["sd_miles"] == 500


# --- persistence across restarts -------------------------------------------------------------------

def test_baseline_persists_across_restart_and_skips_the_log(tmp_path, capsys):
    (tmp_path / "log.jsonl").write_text(line(rec(40, 100, at="2026-10-04T00:00:00Z")))
    store, _ = make_store(tmp_path)
    store.ingest(rec(45, 110, at="2026-10-05T00:00:00Z"))
    saved = json.loads((tmp_path / "shim-state.json").read_text())["sd_baselines"][VIN]
    assert saved["sd_miles"] == 40 and saved["source"] == "record_log"
    capsys.readouterr()
    (tmp_path / "log.jsonl").write_text(line(rec(1, 1, at="2026-01-01T00:00:00Z")))   # an "earlier" record appears: must not win
    again, _ = make_store(tmp_path)                              # restart / add-on update
    again.ingest(rec(None, None, Soc=50))                       # the car reconnects: no counters yet
    assert since(again)["baseline"]["sd_miles"] == 40           # the stored baseline is already in place
    again.ingest(rec(50, 120, at="2026-10-06T00:00:00Z"))
    s = since(again)
    assert s["baseline"]["at"] == "2026-10-04T00:00:00Z" and s["share_pct"] == 50.0    # 10 of 20
    assert "self-driving baseline" not in capsys.readouterr().out


def test_persisting_keeps_other_shim_state_keys(tmp_path):
    (tmp_path / "shim-state.json").write_text(json.dumps({"refresh_token": "t", "telemetry_fields_hash": "h"}))
    store, _ = make_store(tmp_path, log=False)
    store.ingest(rec(1, 2))
    saved = json.loads((tmp_path / "shim-state.json").read_text())
    assert saved["refresh_token"] == "t" and saved["telemetry_fields_hash"] == "h" and VIN in saved["sd_baselines"]


def test_corrupt_or_unwritable_state_does_not_break_ingest(tmp_path):
    (tmp_path / "shim-state.json").write_text("{not json")
    store, _ = make_store(tmp_path, log=False)
    store.ingest(rec(1, 2))
    assert since(store)["baseline"] is not None
    (tmp_path / "shim-state.json").write_text(json.dumps({"sd_baselines": {VIN: {"sd_miles": "x"}}}))   # garbage entry ignored
    store2, _ = make_store(tmp_path, log=False)
    store2.ingest(rec(5, 6))
    assert since(store2)["baseline"]["sd_miles"] == 5


# --- counter reset: re-anchor ----------------------------------------------------------------------

@pytest.mark.parametrize("new,why", [((3, 210), "self-driving"), ((120, 5), "total")])
def test_counter_reset_reanchors_baseline_and_records_why(tmp_path, capsys, new, why):
    store, _ = make_store(tmp_path, log=False)
    store.ingest(rec(100, 200, at="2026-10-30T00:00:00Z"))
    store.ingest(rec(110, 210, at="2026-10-30T01:00:00Z"))
    assert since(store)["share_pct"] == 100.0
    store.ingest(rec(*new, at="2026-10-31T00:00:00Z"))          # a counter dropped below its baseline
    s = since(store)
    assert s["baseline"]["source"] == "reanchor" and s["baseline"]["at"] == "2026-10-31T00:00:00Z"
    assert (s["baseline"]["sd_miles"], s["baseline"]["total_miles"]) == new
    assert s["share_pct"] is None and s["reason"] == "under_1_mile"          # never negative or nonsense
    assert s["reanchor"]["at"] == "2026-10-31T00:00:00Z" and why in s["reanchor"]["reason"]
    assert (s["reanchor"]["previous_sd_miles"], s["reanchor"]["previous_total_miles"]) == (100, 200)
    assert "re-anchored" in capsys.readouterr().out
    store.ingest(rec(new[0] + 2, new[1] + 4, at="2026-10-31T01:00:00Z"))
    assert since(store)["share_pct"] == 50.0                    # measured from the new anchor


def test_reanchor_survives_restart(tmp_path):
    store, _ = make_store(tmp_path, log=False)
    store.ingest(rec(100, 200))
    store.ingest(rec(2, 3, at="2026-11-01T00:00:00Z"))
    again, _ = make_store(tmp_path, log=False)
    again.ingest(rec(None, None, Soc=50))
    s = since(again)
    assert s["baseline"]["sd_miles"] == 2 and s["reanchor"]["at"] == "2026-11-01T00:00:00Z"


def test_stored_baseline_above_current_counters_reanchors_on_first_reading(tmp_path):
    store, _ = make_store(tmp_path, log=False)
    store.ingest(rec(100, 200))
    again, _ = make_store(tmp_path, log=False)
    again.ingest(rec(1, 2, at="2026-11-02T00:00:00Z"))          # the car reset while the add-on was down
    assert since(again)["baseline"]["source"] == "reanchor"


def test_log_baseline_above_current_counters_reanchors_immediately(tmp_path):
    (tmp_path / "log.jsonl").write_text(line(rec(500, 600, at="2026-09-01T00:00:00Z")))
    store, _ = make_store(tmp_path)
    store.ingest(rec(4, 9, at="2026-10-01T00:00:00Z"))
    b = since(store)["baseline"]
    assert b["source"] == "reanchor" and (b["sd_miles"], b["total_miles"]) == (4, 9)


# --- resends and repeats ---------------------------------------------------------------------------

def test_resend_never_seeds_moves_or_reanchors_the_baseline(tmp_path):
    store, _ = make_store(tmp_path, log=False)
    store.ingest(rec(100, 200, resend=True))                     # a buffered replay as the very first record
    assert since(store)["baseline"] is None
    store.ingest(rec(100, 200, at="2026-10-30T00:00:00Z"))
    store.ingest(rec(5, 6, resend=True, at="2026-10-29T00:00:00Z"))   # an old, lower reading replayed later
    s = since(store)
    assert s["baseline"]["sd_miles"] == 100 and s["baseline"]["source"] == "live" and s["reanchor"] is None


def test_repeated_value_changes_nothing(tmp_path):
    store, _ = make_store(tmp_path, log=False)
    store.ingest(rec(100, 200))
    first = since(store)
    store.ingest(rec(100, 200, at="2026-10-30T09:00:00Z"))
    assert since(store) == first and first["total_miles_gained"] == 0


# --- API -------------------------------------------------------------------------------------------

def test_api_payload_carries_since_tracking(tmp_path):
    store, _ = make_store(tmp_path, log=False)
    store.ingest(rec(15982.12890625, 16470.6015625))
    store.ingest(rec(15992.12890625, 16482.6015625, at="2026-10-30T06:00:00Z", Gear="ShiftStateD", VehicleSpeed=50))
    sd = api.state_payload(store)["vehicles"][0]["self_driving"]
    st = sd["since_tracking"]
    assert st["baseline"] == {"sd_miles": 15982.12890625, "total_miles": 16470.6015625,
                              "at": "2026-10-30T04:55:34Z", "source": "live"}
    assert st["sd_miles_gained"] == 10 and st["total_miles_gained"] == 12 and st["share_pct"] == 83.33
    assert st["reason"] is None and st["reanchor"] is None
    assert sd["share_pct"] == 97.02 and sd["miles"] == 15992.12890625          # existing share, now 2 dp
    json.dumps(api.state_payload(store))                                        # serialisable for the SSE feed
    plain = state.Store()
    for r in conftest.load_records():
        plain.ingest(r)
    assert api.state_payload(plain)["vehicles"][0]["self_driving"]["since_tracking"]["reason"] == "no_baseline"


def test_existing_share_is_two_decimals_half_up():
    store = state.Store()
    store.ingest(rec(97, 100))
    assert store.self_driving(VIN)["share_pct"] == 97.0
    store.ingest(rec(1, 800, at="2026-10-30T05:00:00Z", vin=OTHER))
    assert store.self_driving(OTHER)["share_pct"] == 0.13

