"""Single follow-the-records-file implementation, shared by every consumer.

The fleet-telemetry binary's logger output is teed to a JSONL file; the dashboard, the TeslaMate
shim and the streaming bridge all follow it. This replaces three near-identical hand-rolled tail
loops with one generator. Behavior is preserved exactly: read from the start of the file (it is
truncated at boot), tolerate the file being absent, and restart from the top if it is truncated
or rotated under us.
"""
import json
import os
import time


def parse_line(line):
    """Parse one raw line into a dict, or return None to skip it (blank / non-JSON / malformed)."""
    line = line.strip()
    if not line or line[0] != "{":
        return None
    try:
        return json.loads(line)
    except ValueError:
        return None


def tail(path, poll=0.5):
    """Follow `path` forever, yielding each parsed JSON record. Never raises on I/O errors."""
    pos = 0
    while True:
        try:
            if not os.path.exists(path):
                time.sleep(1.0)
                continue
            with open(path, "r", errors="replace") as fh:
                fh.seek(0, os.SEEK_END)
                if fh.tell() < pos:          # truncated / rotated -> restart from the top
                    pos = 0
                fh.seek(pos)
                while True:
                    line = fh.readline()
                    if not line:
                        pos = fh.tell()
                        try:
                            if os.path.getsize(path) < pos:
                                break
                        except OSError:
                            break
                        time.sleep(poll)
                        continue
                    obj = parse_line(line)
                    if obj is not None:
                        yield obj
        except OSError:
            time.sleep(1.0)
