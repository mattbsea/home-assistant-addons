"""One stream-monitor tick for a single VIN.

Extracted from main.stream_monitor so it's unit-testable. The monitor calls the Fleet API ONLY when
the telemetry stream isn't delivering — to bridge charge/state while the stream is down, and to
confirm (and RE-confirm) sleep. `/products` never wakes the car, so re-confirming a sleeping car is
safe; the alternative — latching the first non-online reading until telemetry resumes — left a stale
'offline' stuck even after the car settled to 'asleep'.
"""
import time


def bridge_or_confirm_sleep(store, vin, *, settle, streaming_quiet, settle_secs, bridged, max_bridge,
                            sleep_recheck_secs, poll, sleep=time.sleep, log=None):
    """Decide whether to call the Fleet API for `vin` and fold the result into the Store.

    `poll()` performs the actual Fleet call (token + /products, seeding the Store if online) and
    returns the Fleet state ('online'/'asleep'/'offline'/...) or None on failure. `bridged` is a
    vin->count dict tracking consecutive online-but-stream-down bridge polls (soft-capped).

    Returns an action string (for logging/tests): 'streaming' | 'latched' | 'capped' | 'poll_failed'
    | 'bridged' | 'confirmed'.
    """
    if store.streaming(vin, streaming_quiet):
        bridged.pop(vin, None)
        return "streaming"                                  # stream healthy -> no Fleet call

    if store.sleep_state(vin) is not None:
        # Already confirmed asleep/offline. Re-confirm only on a periodic tick (not a DISCONNECTED
        # nudge) and only once the confirm has gone stale, so an offline<->asleep<->online change Tesla
        # reports while the car is silent gets picked up instead of latching forever.
        if settle or not store.sleep_recheck_due(vin, sleep_recheck_secs):
            return "latched"
    else:
        if bridged.get(vin, 0) >= max_bridge:
            return "capped"                                 # soft cap -> pause until the stream returns
        if settle:
            sleep(settle_secs)                              # ride out a transient drop
            if store.streaming(vin, streaming_quiet):
                bridged.pop(vin, None)
                return "streaming"

    st = poll()
    if st is None:
        return "poll_failed"
    if st == "online":
        bridged[vin] = bridged.get(vin, 0) + 1
        if bridged[vin] == 1 and log:
            log(f"[app] bridging {vin} via Fleet API (telemetry stream down)")
        return "bridged"
    prev = store.sleep_state(vin)
    store.set_sleep_state(vin, st)
    bridged.pop(vin, None)
    if st != prev and log:
        log(f"[app] {vin} '{st}' (stream down) — reporting to TeslaMate")
    return "confirmed"
