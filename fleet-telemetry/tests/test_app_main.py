"""Phase 3 — the unified app entrypoint: one tail feeds the Store; build() wires the shim app."""
import importlib
import time

from starlette.testclient import TestClient

main = importlib.import_module("app.main")

VIN = "7SAYGDEE3PF884783"


def test_build_returns_working_shim_app():
    store, registry, app = main.build()
    store.ingest({"msg": "record_payload", "vin": VIN,
                  "data": {"Soc": 50.0, "Location": {"latitude": 47.77, "longitude": -122.15}, "Vin": VIN}})
    c = TestClient(app)
    body = c.get("/api/1/products").json()
    assert body["count"] == 1 and body["response"][0]["vin"] == VIN


def test_start_ingest_tails_file_into_store(tmp_path):
    f = tmp_path / "records.jsonl"
    f.write_text(
        '{"msg":"record_payload","vin":"%s","data":{"Soc":42.0,"Vin":"%s"}}\n' % (VIN, VIN)
    )
    store, _, _ = main.build()
    main.start_ingest(store, str(f))
    for _ in range(50):                      # give the tail thread a moment
        if VIN in store.vins():
            break
        time.sleep(0.05)
    assert store.snapshot(VIN).get("Soc") == 42.0
