"""Phase 3 — Fleet-API cold-start priming orchestration (HTTP injected)."""
import importlib
import time

import conftest

state = importlib.import_module("app.state")
shim_rest = importlib.import_module("app.sinks.shim_rest")
prime = importlib.import_module("app.control.prime")

VIN = "7SAYGDEE3PF884783"


def _fakes(products, vehicle_data, *, new_rt="rotated-token"):
    calls = {"token": 0}

    def post_form(url, data):
        calls["token"] += 1
        return {"access_token": "AT", "refresh_token": new_rt}

    def get(url, token):
        if url.endswith("/products"):
            return {"response": products}
        return {"response": vehicle_data}
    return post_form, get, calls


def test_prime_sets_registry_prime_for_online_vehicle():
    store = state.Store()
    reg = shim_rest.Registry(store)
    vd = conftest.load_reference("shim_vehicle_data.json")["response"]
    products = [{"vin": VIN, "id": 999, "vehicle_id": 1, "state": "online", "display_name": "DoodleMobile"}]
    post_form, get, _ = _fakes(products, vd)

    rotated = []
    n = prime.prime_once(reg, client_id="cid", refresh_token="seed",
                         auth_host="https://auth", fleet_host="https://fleet",
                         post_form=post_form, get=get, on_token=rotated.append, log=lambda *_: None)
    assert n == 1
    m = reg._m(VIN)
    assert m["tesla_id"] == 999 and m["display_name"] == "DoodleMobile"
    assert store.snapshot(VIN)["Soc"] == 52       # seed folded into the unified field map
    assert rotated == ["rotated-token"]          # rotation surfaced
    # primed vehicle is now "ready" even with no telemetry yet
    assert reg.ready(VIN) is True


def test_fetch_charge_fields_writes_pilot_and_brand():
    store = state.Store()

    def post_form(url, data):
        return {"access_token": "AT"}

    def get(url, token):
        return {"response": {"charge_state": {"charger_pilot_current": 48, "fast_charger_brand": "Tesla"}}}
    prime.fetch_charge_fields(store, vin=VIN, tesla_id=999, client_id="c", refresh_token="r",
                              auth_host="a", fleet_host="f", post_form=post_form, get=get, log=lambda *_: None)
    snap = store.snapshot(VIN)
    assert snap["ChargerPilotCurrent"] == 48 and snap["FastChargerBrand"] == "Tesla"


def test_fetch_charge_fields_skips_invalid_brand():
    store = state.Store()
    prime.fetch_charge_fields(store, vin=VIN, tesla_id=999, client_id="c", refresh_token="r",
                              auth_host="a", fleet_host="f",
                              post_form=lambda u, d: {"access_token": "AT"},
                              get=lambda u, t: {"response": {"charge_state": {"fast_charger_brand": "<invalid>"}}},
                              log=lambda *_: None)
    assert "FastChargerBrand" not in store.snapshot(VIN)   # AC charging -> no brand, not "<invalid>"


def _store_with_data():
    s = state.Store()
    s.ingest({"msg": "record_payload", "vin": VIN,
              "data": {"Soc": 50, "Location": {"latitude": 47.4, "longitude": -122.2}}})
    return s


def test_products_state_returns_vehicle_state():
    st = prime.products_state(vin=VIN, client_id="c", refresh_token="r", auth_host="a", fleet_host="f",
                              post_form=lambda u, d: {"access_token": "AT"},
                              get=lambda u, t: {"response": [{"vin": VIN, "state": "asleep"}]},
                              log=lambda *_: None)
    assert st == "asleep"


def test_confirm_sleep_sets_state_when_confirmed_offline():
    s = _store_with_data()
    st = prime.confirm_sleep(s, VIN, time.time(), client_id="c", refresh_token="r", auth_host="a",
                             fleet_host="f", settle_secs=0, sleeper=lambda _: None,
                             post_form=lambda u, d: {"access_token": "AT"},
                             get=lambda u, t: {"response": [{"vin": VIN, "state": "asleep"}]},
                             log=lambda *_: None)
    assert st == "asleep" and s.vehicle_state(VIN) == "asleep"


def test_confirm_sleep_aborts_when_reconnected_during_window():
    s = _store_with_data()
    disc = s.vehicles[VIN]["last_data_epoch"] - 10   # disconnect "before" the last data => reconnected
    polled = {"n": 0}

    def get(u, t):
        polled["n"] += 1
        return {"response": [{"vin": VIN, "state": "asleep"}]}
    st = prime.confirm_sleep(s, VIN, disc, client_id="c", refresh_token="r", auth_host="a", fleet_host="f",
                             settle_secs=0, sleeper=lambda _: None, post_form=lambda u, d: {"access_token": "AT"},
                             get=get, log=lambda *_: None)
    assert st is None and s.vehicle_state(VIN) == "online" and polled["n"] == 0   # no /products poll


def test_confirm_sleep_no_action_when_online():
    s = _store_with_data()
    st = prime.confirm_sleep(s, VIN, time.time(), client_id="c", refresh_token="r", auth_host="a",
                             fleet_host="f", settle_secs=0, sleeper=lambda _: None,
                             post_form=lambda u, d: {"access_token": "AT"},
                             get=lambda u, t: {"response": [{"vin": VIN, "state": "online"}]},
                             log=lambda *_: None)
    assert st is None and s.vehicle_state(VIN) == "online"


def test_confirm_sleep_handles_products_failure():
    s = _store_with_data()

    def boom(u, t):
        raise RuntimeError("net")
    st = prime.confirm_sleep(s, VIN, time.time(), client_id="c", refresh_token="r", auth_host="a",
                             fleet_host="f", settle_secs=0, sleeper=lambda _: None,
                             post_form=lambda u, d: {"access_token": "AT"}, get=boom, log=lambda *_: None)
    assert st is None and s.vehicle_state(VIN) == "online"   # failure -> staleness backstop only


def test_prime_skips_asleep_and_handles_no_creds():
    store = state.Store()
    reg = shim_rest.Registry(store)
    products = [{"vin": VIN, "id": 1, "vehicle_id": 1, "state": "asleep"}]
    post_form, get, _ = _fakes(products, {})
    assert prime.prime_once(reg, client_id="c", refresh_token="r", auth_host="a", fleet_host="f",
                            post_form=post_form, get=get, log=lambda *_: None) == 0
    assert store.snapshot(VIN) == {}             # asleep vehicle never seeded
    # no creds -> no-op
    assert prime.prime_once(reg, client_id="", refresh_token="", auth_host="a", fleet_host="f",
                            log=lambda *_: None) == 0
