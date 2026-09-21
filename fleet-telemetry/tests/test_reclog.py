"""Append-only persistent telemetry-capture log (app/reclog.py)."""
import gzip
import json

from app import reclog


def test_writes_records_as_jsonl_lines(tmp_path):
    p = tmp_path / "telemetry.jsonl"
    log = reclog.RecordLog(str(p))
    log.write({"msg": "record_payload", "vin": "ABC", "data": {"Gear": "ShiftStateP"}})
    log.write({"msg": "record_payload", "vin": "ABC", "data": {"Gear": "ShiftStateD"}})
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["data"]["Gear"] == "ShiftStateP"
    assert json.loads(lines[1])["data"]["Gear"] == "ShiftStateD"


def test_creates_parent_directory(tmp_path):
    p = tmp_path / "nested" / "dir" / "telemetry.jsonl"
    log = reclog.RecordLog(str(p))
    log.write({"a": 1})
    assert p.exists()


def test_append_only_below_threshold_never_rotates(tmp_path):
    p = tmp_path / "telemetry.jsonl"
    log = reclog.RecordLog(str(p))  # default 500 MiB threshold; this test writes a few KB
    for i in range(500):
        log.write({"i": i, "pad": "x" * 50})
    lines = p.read_text().splitlines()
    # Every record is retained, in order — nothing dropped, nothing rotated away.
    assert len(lines) == 500
    assert json.loads(lines[0])["i"] == 0
    assert json.loads(lines[-1])["i"] == 499
    # No archive is created below the size threshold.
    assert list(tmp_path.glob("telemetry.jsonl.*.gz")) == []


def test_rotates_and_gzips_once_max_bytes_exceeded(tmp_path):
    p = tmp_path / "telemetry.jsonl"
    log = reclog.RecordLog(str(p), max_bytes=200)  # tiny threshold, easy to cross deterministically
    for i in range(10):
        log.write({"i": i, "pad": "x" * 20})       # each line well over 20 bytes -> crosses 200 fast
    # Exactly one archive was produced (still under the second rotation's worth of data).
    archives = sorted(tmp_path.glob("telemetry.jsonl.*.gz"))
    assert len(archives) == 1
    with gzip.open(archives[0], "rt") as fh:
        archived_lines = fh.read().splitlines()
    # The current (post-rotation) file plus the archive together retain every record, in order —
    # rotation never drops or reorders data, it only relocates it.
    current_lines = p.read_text().splitlines()
    all_records = [json.loads(l)["i"] for l in archived_lines + current_lines]
    assert all_records == list(range(10))
    assert current_lines, "a fresh file must be open and writable after rotation"


def test_rotation_failure_falls_back_to_appending_same_file(tmp_path, monkeypatch):
    p = tmp_path / "telemetry.jsonl"
    log = reclog.RecordLog(str(p), max_bytes=1)  # rotate on every write
    monkeypatch.setattr(reclog.gzip, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    log.write({"i": 0})
    log.write({"i": 1})  # rotation fails silently each time; writes must still land, nothing raises
    lines = p.read_text().splitlines()
    assert [json.loads(l)["i"] for l in lines] == [0, 1]
    assert list(tmp_path.glob("telemetry.jsonl.*.gz")) == []


def test_append_continues_across_reopen(tmp_path):
    p = tmp_path / "telemetry.jsonl"
    reclog.RecordLog(str(p)).write({"first": True})
    reclog.RecordLog(str(p)).write({"second": True})  # fresh instance, same path: must not truncate
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"first": True}
    assert json.loads(lines[1]) == {"second": True}


def test_write_never_raises_on_unwritable_path():
    # Logging must never take down ingest; a bad path is swallowed.
    log = reclog.RecordLog("/proc/cannot/create/here/telemetry.jsonl")
    log.write({"a": 1})  # must not raise
