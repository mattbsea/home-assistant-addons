"""Phase 3 — dashboard data API (/api/state) built from the Store."""
import importlib

import conftest

state = importlib.import_module("app.state")
api = importlib.import_module("app.web.api")

VIN = "7SAYGDEE3PF884783"


def test_state_payload_shape():
    store = state.Store()
    for r in conftest.load_records():
        store.ingest(r)
    p = api.state_payload(store, version="1.0.0", cert={"days_left": 90}, namespace="tesla_telemetry")
    assert p["version"] == "1.0.0" and p["namespace"] == "tesla_telemetry"
    assert p["total_records"] == len(conftest.load_records())
    assert p["records_per_min"] >= 0
    assert len(p["vehicles"]) == 1
    v = p["vehicles"][0]
    assert v["vin"] == VIN
    assert v["fields"]["Soc"]["value"] == 51.85185185185185      # full {value,created_at,received_at}
    assert v["location"] == {"lat": 47.768839, "lon": -122.155053}
    assert 51.85 in v["soc_history"]            # history rounded to 2dp (matches v0)
    assert v["client_version"] == "1.2.0"                        # captured from record metadata
    assert v["last_seen_epoch"] > 0
