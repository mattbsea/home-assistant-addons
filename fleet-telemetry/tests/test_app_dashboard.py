"""Phase 3 — the dashboard served from the app (static page + /api/state)."""
import importlib

import conftest
from starlette.testclient import TestClient

state = importlib.import_module("app.state")
dashboard = importlib.import_module("app.web.dashboard")

VIN = "7SAYGDEE3PF884783"


def test_dashboard_page_and_state():
    store = state.Store()
    for r in conftest.load_records():
        store.ingest(r)
    app = dashboard.build_dashboard_app(store, version="1.0.0",
                                        cert_getter=lambda: {"days_left": 90}, namespace="tesla_telemetry")
    c = TestClient(app)

    page = c.get("/")
    assert page.status_code == 200
    assert "<!DOCTYPE html>" in page.text
    assert "text/html" in page.headers["content-type"]

    st = c.get("/api/state").json()
    assert st["version"] == "1.0.0"
    assert st["cert"] == {"days_left": 90}
    assert st["vehicles"][0]["vin"] == VIN
    assert st["vehicles"][0]["fields"]["Soc"]["value"] == 51.85185185185185
