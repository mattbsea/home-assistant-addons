#!/usr/bin/env python3
"""Self-hosted glue: fleet-telemetry logger records -> MyTeslaMate websocket server.

Normally Google Pub/Sub pushes fleet-telemetry records to the MyTeslaMate websocket server's
`POST /` endpoint as `{"message":{"data": base64(payload)}}`. To stay fully self-hosted we skip
Pub/Sub: this tails the same logger output the dashboard uses, rebuilds the fleet-telemetry
protojson Payload the server expects, and POSTs it directly. stdlib only (urllib), so no new deps.

Env:
  FT_RECORDS_FILE   logger output to tail (default /tmp/ft-records.jsonl)
  FT_BRIDGE_URL     websocket server ingest URL, e.g. http://192.168.161.3:8081/
"""

import base64
import json
import os
import time
import urllib.request

RECORDS_FILE = os.environ.get("FT_RECORDS_FILE", "/tmp/ft-records.jsonl")
BRIDGE_URL = os.environ.get("FT_BRIDGE_URL", "").strip()
_META = {"CreatedAt", "IsResend", "Vin"}


def log(msg):
    print(f"[fleet-telemetry-bridge] {msg}", flush=True)


def _gear_letter(v):
    """Map any gear representation (DriveGearP / ShiftStateP / P / Drive…) to P/R/N/D."""
    s = str(v).upper()
    if s and s[-1] in "PRND":
        return s[-1]
    if "PARK" in s:
        return "P"
    if "REV" in s:
        return "R"
    if "NEUT" in s:
        return "N"
    if "DRIVE" in s:
        return "D"
    return s


def to_payload(rec):
    """Convert a logger 'record_payload' into the protojson Payload the websocket server expects."""
    data = rec.get("data") or {}
    vin = rec.get("vin") or data.get("Vin")
    if not vin:
        return None
    items = []
    for key, value in data.items():
        if key in _META:
            continue
        if key == "Location" and isinstance(value, dict):
            lat = value.get("latitude", value.get("Latitude"))
            lon = value.get("longitude", value.get("Longitude"))
            if lat is not None and lon is not None:
                items.append({"key": "Location",
                              "value": {"locationValue": {"latitude": lat, "longitude": lon}}})
                continue
        if key == "Gear":
            items.append({"key": "Gear",
                          "value": {"shiftStateValue": "ShiftState" + _gear_letter(value)}})
            continue
        if isinstance(value, bool):
            items.append({"key": key, "value": {"booleanValue": value}})
        elif isinstance(value, (int, float)):
            items.append({"key": key, "value": {"doubleValue": float(value)}})
        else:
            items.append({"key": key, "value": {"stringValue": str(value)}})
    if not items:
        return None
    return {"vin": vin, "createdAt": data.get("CreatedAt"), "data": items}


def post(payload):
    body = json.dumps({
        "message": {"data": base64.b64encode(json.dumps(payload).encode()).decode()}
    }).encode()
    req = urllib.request.Request(BRIDGE_URL, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=8) as resp:
        resp.read()


def tail():
    pos = 0
    warned = False
    while True:
        try:
            if not os.path.exists(RECORDS_FILE):
                time.sleep(1.0)
                continue
            with open(RECORDS_FILE, "r", errors="replace") as fh:
                fh.seek(0, os.SEEK_END)
                if fh.tell() < pos:
                    pos = 0
                fh.seek(pos)
                while True:
                    line = fh.readline()
                    if not line:
                        pos = fh.tell()
                        try:
                            if os.path.getsize(RECORDS_FILE) < pos:
                                break
                        except OSError:
                            break
                        time.sleep(0.5)
                        continue
                    line = line.strip()
                    if not line or line[0] != "{":
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if obj.get("msg") != "record_payload":
                        continue
                    payload = to_payload(obj)
                    if not payload:
                        continue
                    try:
                        post(payload)
                        warned = False
                    except Exception as e:
                        if not warned:
                            log(f"POST to {BRIDGE_URL} failed ({e}); will keep retrying as records arrive")
                            warned = True
        except OSError:
            time.sleep(1.0)


def main():
    if not BRIDGE_URL:
        log("FT_BRIDGE_URL not set; bridge disabled")
        return
    log(f"forwarding telemetry to {BRIDGE_URL}")
    tail()


if __name__ == "__main__":
    main()
