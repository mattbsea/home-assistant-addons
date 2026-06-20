"""The stream monitor's per-VIN tick: bridge while the stream is down, and re-confirm sleep.

Extracted from main.stream_monitor so the decision logic (esp. the sleep re-confirm that fixes the
stale-latch bug) is unit-testable with a real Store + an injected Fleet poll.
"""
import importlib
import time

state = importlib.import_module("app.state")
monitor = importlib.import_module("app.control.monitor")

VIN = "7SAYGDEE3PF884783"


def _stale_vehicle(store):
    """A vehicle whose telemetry has gone silent (stream down) so the monitor will consider polling."""
    store.ingest({"msg": "record_payload", "vin": VIN, "data": {"Soc": 50}})
    store.vehicles[VIN]["last_data_epoch"] = time.time() - 10000


def _tick(store, *, settle=False, poll, bridged=None):
    return monitor.bridge_or_confirm_sleep(
        store, VIN, settle=settle, streaming_quiet=90, settle_secs=0,
        bridged=bridged if bridged is not None else {}, max_bridge=100,
        sleep_recheck_secs=600, poll=poll, sleep=lambda _s: None)


def test_streaming_skips_fleet_poll():
    store = state.Store()
    store.ingest({"msg": "record_payload", "vin": VIN, "data": {"Soc": 50}})   # fresh telemetry
    calls = []
    act = _tick(store, poll=lambda: calls.append(1) or "online")
    assert act == "streaming" and calls == []


def test_latched_sleep_not_rechecked_before_interval():
    store = state.Store()
    _stale_vehicle(store)
    store.set_sleep_state(VIN, "offline")
    calls = []
    act = _tick(store, poll=lambda: calls.append(1) or "asleep")
    assert act == "latched" and calls == []                  # confirmed recently -> no Fleet call


def test_latched_sleep_rechecked_when_stale_picks_up_transition():
    """The bug: once offline/asleep is set the old monitor never re-polled, so a stale 'offline' stuck
    even after the car settled to 'asleep'. Now a stale confirm triggers a /products re-check that
    refreshes the state."""
    store = state.Store()
    _stale_vehicle(store)
    store.set_sleep_state(VIN, "offline")
    store.vehicles[VIN]["sleep_state_epoch"] = time.time() - 601   # confirm went stale
    calls = []
    act = _tick(store, poll=lambda: calls.append(1) or "asleep")
    assert calls == [1]                                       # re-polled /products
    assert store.sleep_state(VIN) == "asleep"                 # offline -> asleep refreshed
    assert act == "confirmed"


def test_latched_recheck_clears_when_back_online():
    store = state.Store()
    _stale_vehicle(store)
    store.set_sleep_state(VIN, "offline")
    store.vehicles[VIN]["sleep_state_epoch"] = time.time() - 601

    def poll():
        store.seed(VIN, {"charge_state": {"battery_level": 60},
                         "drive_state": {"latitude": 47.4, "longitude": -122.2}})   # seed clears sleep
        return "online"
    act = _tick(store, poll=poll)
    assert act == "bridged" and store.sleep_state(VIN) is None


def test_not_latched_confirms_sleep_from_products():
    store = state.Store()
    _stale_vehicle(store)
    act = _tick(store, poll=lambda: "asleep")
    assert act == "confirmed" and store.sleep_state(VIN) == "asleep"


def test_not_latched_bridges_when_online():
    store = state.Store()
    _stale_vehicle(store)
    bridged = {}
    act = _tick(store, poll=lambda: "online", bridged=bridged)
    assert act == "bridged" and bridged[VIN] == 1


def test_soft_cap_pauses_bridging():
    store = state.Store()
    _stale_vehicle(store)
    calls = []
    act = _tick(store, poll=lambda: calls.append(1) or "online", bridged={VIN: 100})
    assert act == "capped" and calls == []


def test_poll_failure_returns_without_state_change():
    store = state.Store()
    _stale_vehicle(store)
    act = _tick(store, poll=lambda: None)
    assert act == "poll_failed" and store.sleep_state(VIN) is None
