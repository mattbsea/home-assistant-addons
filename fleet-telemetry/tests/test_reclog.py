"""Append-only persistent telemetry-capture log (app/reclog.py)."""
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


def test_append_only_never_truncates_or_rotates(tmp_path):
    p = tmp_path / "telemetry.jsonl"
    log = reclog.RecordLog(str(p))
    for i in range(500):
        log.write({"i": i, "pad": "x" * 50})
    lines = p.read_text().splitlines()
    # Every record is retained, in order — nothing dropped, nothing rotated away.
    assert len(lines) == 500
    assert json.loads(lines[0])["i"] == 0
    assert json.loads(lines[-1])["i"] == 499
    # No backup/rotation file is ever created.
    assert not (tmp_path / "telemetry.jsonl.1").exists()


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
