"""Phase 3 — Fleet-API cold-start priming orchestration (HTTP injected)."""
import importlib

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
    assert m["prime"]["charge_state"]["battery_level"] == 52
    assert rotated == ["rotated-token"]          # rotation surfaced
    # primed vehicle is now "ready" even with no telemetry yet
    assert reg.ready(VIN) is True


def test_prime_skips_asleep_and_handles_no_creds():
    store = state.Store()
    reg = shim_rest.Registry(store)
    products = [{"vin": VIN, "id": 1, "vehicle_id": 1, "state": "asleep"}]
    post_form, get, _ = _fakes(products, {})
    assert prime.prime_once(reg, client_id="c", refresh_token="r", auth_host="a", fleet_host="f",
                            post_form=post_form, get=get, log=lambda *_: None) == 0
    assert reg._m(VIN)["prime"] is None
    # no creds -> no-op
    assert prime.prime_once(reg, client_id="", refresh_token="", auth_host="a", fleet_host="f",
                            log=lambda *_: None) == 0
