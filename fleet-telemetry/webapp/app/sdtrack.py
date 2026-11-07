"""Self-driving share "since tracking began": the persisted baseline and its seeding from the record log.

The baseline is the earliest (SelfDrivingMilesSinceReset, MilesSinceReset) pair the add-on has for a
vehicle. It is stored per VIN under `sd_baselines` in shim-state.json (the unwatched operational state
file, so writing it never bounces the telemetry binary) and so survives restarts and updates. When none
is stored yet it is seeded from the persistent record log (/data/telemetry-log.jsonl plus its gzip
rotations, oldest first), else from the first live reading (see state.Store._sd_step). A `(0, 0)` pair, or
any non-finite/negative value, is not a reading: it is skipped when seeding from the log and never moves
the baseline at ingest.
"""
import glob
import gzip
import json
import os
import threading
import zlib

import fields
from app.control import tokens

SD_KEY, TOTAL_KEY = "SelfDrivingMilesSinceReset", "MilesSinceReset"
STATE_KEY = "sd_baselines"
MAX_LINE = 1 << 20   # a longer "line" is corrupt (records are a few KB); skip it instead of buffering it

# Serialise the read-modify-write of the sd_baselines sub-dict (tokens.write_state only merges whole keys).
_LOCK = threading.Lock()


def _lines(fh):
    """Yield lines, never holding more than MAX_LINE bytes of one: an over-long line (corrupt or
    newline-less file) is dropped whole."""
    while True:
        line = fh.readline(MAX_LINE)
        if not line:
            return
        if len(line) == MAX_LINE and not line.endswith("\n"):
            while True:                      # drain the rest of the over-long line
                rest = fh.readline(MAX_LINE)
                if not rest or rest.endswith("\n"):
                    break
            continue
        yield line


def log_files(path):
    """The record log's files, oldest first: gzip rotations (`<path>.<UTC stamp>.gz`, which sort
    chronologically by name) then the live file. Missing files are simply absent."""
    files = sorted(glob.glob(glob.escape(path) + ".*.gz"))
    if os.path.isfile(path):
        files.append(path)
    return files


def _match(line, vin):
    """The baseline pair carried by one log line, or None. A cheap substring test comes first so the
    (rare) matching lines are the only ones parsed. IsResend records are skipped: they replay older
    readings and must never move the baseline."""
    if f'"{SD_KEY}"' not in line or f'"{TOTAL_KEY}"' not in line:
        return None
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if not isinstance(rec, dict) or rec.get("msg") != "record_payload":
        return None
    data = rec.get("data")
    if not isinstance(data, dict) or data.get("IsResend") or (rec.get("vin") or data.get("Vin")) != vin:
        return None
    sd, total = fields.num(data.get(SD_KEY)), fields.num(data.get(TOTAL_KEY))
    if not fields.usable_counters(sd, total):
        return None      # missing, non-finite/negative, or the (0, 0) glitch pair
    return {"sd_miles": sd, "total_miles": total, "at": data.get("CreatedAt") or None,
            "source": "record_log", "reanchor": None}


def scan_log(path, vin):
    """Baseline from the earliest record in the log that carries both counters for `vin`, or None.

    Streams each file line by line (nothing is loaded whole), stops at the first match in the oldest
    file that has one, tolerates malformed lines, truncated or corrupt archives and a missing file."""
    if not path:
        return None
    for fp in log_files(path):
        try:
            opener = gzip.open if fp.endswith(".gz") else open
            with opener(fp, "rt", encoding="utf-8", errors="replace") as fh:
                for line in _lines(fh):
                    found = _match(line, vin)
                    if found:
                        return found
        except (OSError, EOFError, zlib.error):
            continue    # unreadable or truncated file: move on to the next one
    return None


class BaselineStore:
    """Persistence + log seeding for the baselines of every vehicle."""

    def __init__(self, state_path, log_path):
        self.state_path = state_path
        self.log_path = log_path

    def load(self, vin):
        b = (tokens.read_state(self.state_path).get(STATE_KEY) or {}).get(vin)
        ok = isinstance(b, dict) and fields.num(b.get("sd_miles")) is not None \
            and fields.num(b.get("total_miles")) is not None
        return b if ok else None

    def save(self, vin, baseline):
        with _LOCK:
            all_ = tokens.read_state(self.state_path).get(STATE_KEY)
            all_ = dict(all_) if isinstance(all_, dict) else {}
            all_[vin] = baseline
            tokens.write_state(self.state_path, **{STATE_KEY: all_})

    def discard(self, vin):
        """Remove one vehicle's stored baseline (so it is rebuilt from the record log)."""
        with _LOCK:
            all_ = tokens.read_state(self.state_path).get(STATE_KEY)
            if not isinstance(all_, dict) or vin not in all_:
                return
            all_ = dict(all_)
            all_.pop(vin, None)
            tokens.write_state(self.state_path, **{STATE_KEY: all_})

    def repair_suspect_zero_reanchors(self):
        """Drop stored baselines that are the (0, 0) glitch signature: source 'reanchor' with both
        counters zero. Each is rebuilt from the record log (or the first live reading) on first ingest.
        Other baselines are untouched. Returns the VINs repaired."""
        with _LOCK:
            all_ = tokens.read_state(self.state_path).get(STATE_KEY)
            if not isinstance(all_, dict):
                return []
            bad = [vin for vin, b in all_.items()
                   if isinstance(b, dict) and b.get("source") == "reanchor"
                   and fields.num(b.get("sd_miles")) == 0 and fields.num(b.get("total_miles")) == 0]
            if not bad:
                return []
            all_ = dict(all_)
            for vin in bad:
                all_.pop(vin, None)
            tokens.write_state(self.state_path, **{STATE_KEY: all_})
        for vin in bad:
            print(f"[app] discarding suspect zero self-driving baseline for ...{vin[-4:]} "
                  f"(re-anchor from a (0, 0) reading); rebuilding from the record log (or first live reading)", flush=True)
        return bad

    def scan_log(self, vin):
        return scan_log(self.log_path, vin)
