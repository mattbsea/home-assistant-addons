"""Auto re-send fleet_telemetry_config when the requested-field roster changes.

The roster (fields.TELEMETRY_FIELDS) defines what the car streams, but it only takes effect after a
signed fleet_telemetry_config command. So when the add-on upgrades and the roster changes, the new
fields would otherwise sit idle until the user manually clicked "Send to Vehicle".

This compares a fingerprint of the roster against the last successfully-sent one (kept in
shim-state.json — the UNWATCHED file; never wizard-config.json, which would bounce the binary) and
re-sends when they differ. Safety: a rejected send is inert — Tesla validates the config atomically
and the car keeps streaming its existing config — so a bad roster field can't drop telemetry; the
failed send is logged and retried on a later cycle (the hash is only stored on success).

Fields Tesla's Fleet API does not know yet ("Unknown field <Name>", typically newer proto fields on older
firmware) are dropped by sendconfig and the send retried without them, so the rest still goes through.
State kept in shim-state:
  telemetry_fields_hash   hash of the DESIRED roster (dropped fields included) once a send succeeded. A
                          partial send therefore counts as done and a normal boot re-pushes nothing.
  telemetry_unsupported   {Name: {first_seen, last_tried}} — the fields Tesla rejected. They are held out of
                          routine sends and offered again in full once a day and after an add-on update, so
                          they switch on by themselves when Tesla's API accepts them.
"""
import os
import threading
import time

import fields
from app.control import config as cfgmod, sendconfig, tokens

# How long dropped fields wait before being offered to Tesla again (also retried after an add-on update).
RETRY_SECS = 24 * 3600

# Shown on the dashboard beside the dropped fields.
FIRMWARE_NOTE = ("Tesla rejected these fields as unknown. The newer fields need car firmware 2026.32.300 or "
                 "2026.38.6 (268 needs 2026.32+). The add-on offers them to Tesla again once a day and after "
                 "an add-on update, and they switch on by themselves when accepted.")

_LOCK = threading.Lock()   # one send at a time: the seed loop, the periodic loop and the wizard share this


def _unsupported(state, desired):
    """The recorded unsupported fields that are still in the desired roster (a field the user has since
    disabled is no longer relevant)."""
    raw = state.get("telemetry_unsupported")
    raw = raw if isinstance(raw, dict) else {}
    return {n: (i if isinstance(i, dict) else {}) for n, i in raw.items() if n in desired}


def _last_tried(unsupported):
    return max((float(i.get("last_tried") or 0) for i in unsupported.values()), default=0.0)


def retry_due(state, desired, *, version="", now=None):
    """True when fields Tesla rejected earlier should be offered again: a day since the last try, or the
    add-on version changed since then (an update may carry a newer roster or proto)."""
    unsupported = _unsupported(state, desired)
    if not unsupported:
        return False
    now = time.time() if now is None else now
    if version and state.get("telemetry_unsupported_version") != version:
        return True
    return now - _last_tried(unsupported) >= RETRY_SECS


def record_result(shim_state_path, *, desired, result, held_out=(), version="", now=None):
    """Persist the outcome of a send of `desired` (minus `held_out`, the known-unsupported fields that were
    deliberately not offered). Success stores the roster hash — also for a partial send — so it is not
    re-pushed; failure stores only the fields Tesla named as unknown, never the hash."""
    now = time.time() if now is None else now
    state = tokens.read_state(shim_state_path)
    prior = _unsupported(state, desired)
    ok = bool(result.get("ok"))
    # Success answers for every field offered, so only the held-out ones carry over; a failure answered for
    # none of the rest, so everything recorded before stays.
    new = {n: dict(i) for n, i in prior.items() if not ok or n in held_out}
    for n in result.get("dropped") or []:
        new[n] = {"first_seen": prior.get(n, {}).get("first_seen", now), "last_tried": now}
    updates = {"telemetry_unsupported": new}
    last = {"at": now, "ok": ok, "dropped": list(result.get("dropped") or [])}
    if ok:
        updates["telemetry_fields_hash"] = fields.telemetry_fields_hash(desired)   # only mark sent on success
        updates["telemetry_unsupported_version"] = version
        last["sent_count"] = len(result.get("sent_fields") or [])
    else:
        last["error"] = str(result.get("error") or "")[:300]
    updates["telemetry_last_send"] = last
    tokens.write_state(shim_state_path, **updates)


def config_status(shim_state_path, *, version="", now=None):
    """What the dashboard shows about the config send: the fields Tesla does not accept yet."""
    now = time.time() if now is None else now
    state = tokens.read_state(shim_state_path)
    desired = fields.effective_roster(state.get("telemetry_roster"))
    unsupported = _unsupported(state, desired)
    items = [{"name": n, "first_seen": i.get("first_seen"), "last_tried": i.get("last_tried")}
             for n, i in sorted(unsupported.items())]
    return {"unsupported": items,
            "note": FIRMWARE_NOTE if items else "",
            "next_retry_epoch": (_last_tried(unsupported) + RETRY_SECS) if items else None,
            "last_send": state.get("telemetry_last_send")}


def maybe_resend(*, vins, config_path, shim_state_path, wizard_state_path, cert_file, private_key_path,
                 auth_host="https://auth.tesla.com", log=print, version="", now=None):
    """Re-send the telemetry config iff setup is complete and either the roster fingerprint changed or
    fields Tesla rejected earlier are due another try. Returns "sent" / "failed" / None (no-op). Never raises."""
    if not _LOCK.acquire(blocking=False):
        return None   # another send is in flight
    try:
        if not cfgmod.load_wizard_state(wizard_state_path).get("completed"):
            return None  # setup not finished — the wizard owns the first send
        c = cfgmod.load(config_path).get("tesla", {})
        domain = c.get("telemetry_domain", "")
        if not (c.get("client_id") and domain and os.path.exists(cert_file) and os.path.exists(private_key_path)):
            return None
        if not vins:
            return None  # nothing primed/seen yet; retry next cycle
        state = tokens.read_state(shim_state_path)
        # The roster override lives in shim-state (unwatched) — never wizard-config, which would bounce
        # the binary and drop the telemetry stream on every edit.
        desired = fields.effective_roster(state.get("telemetry_roster"))
        changed = state.get("telemetry_fields_hash") != fields.telemetry_fields_hash(desired)
        retry = retry_due(state, desired, version=version, now=now)
        if not changed and not retry:
            return None  # roster unchanged and no dropped field is due another try
        # Routine sends leave out the fields Tesla already rejected (they would only fail again); a due
        # retry offers the full roster and lets sendconfig drop whatever is still unknown.
        held_out = [] if retry else sorted(_unsupported(state, desired))
        roster = {n: v for n, v in desired.items() if n not in held_out}
        try:
            port = int(c.get("telemetry_port") or 4443)
        except (TypeError, ValueError):
            port = 4443
        rt = state.get("refresh_token") or c.get("shim_refresh_token", "")
        if changed:
            log(f"[autosend] telemetry roster changed — re-sending fleet_telemetry_config to {len(vins)} vehicle(s)")
        else:
            log(f"[autosend] re-trying {len(_unsupported(state, desired))} field(s) Tesla did not accept earlier — "
                f"re-sending fleet_telemetry_config to {len(vins)} vehicle(s)")
        r = sendconfig.send(vins=vins, client_id=c.get("client_id", ""), refresh_token=rt,
                            domain=domain, region=c.get("region", "na"), port=port,
                            cert_file=cert_file, private_key_file=private_key_path, auth_host=auth_host,
                            roster=roster, log=log)
        # Persist any rotated token regardless of outcome (it was rotated during the token refresh).
        if r.get("new_refresh_token"):
            tokens.save(shim_state_path, r["new_refresh_token"])
        if r.get("ok") or r.get("dropped"):
            record_result(shim_state_path, desired=desired, result=r, held_out=held_out, version=version, now=now)
        if r.get("ok"):
            dropped = r.get("dropped") or []
            sent = r.get("sent_fields") or []
            log(f"[autosend] roster re-sent OK: {len(sent)} field(s) sent, "
                f"{len(dropped) + len(held_out)} not supported by Tesla yet"
                + (f" ({', '.join(dropped + held_out)})" if dropped or held_out else "")
                + f" — {r.get('response')}")
            log(f"[autosend] fields sent: {', '.join(sent)}")
            return "sent"
        log(f"[autosend] roster re-send FAILED (inert — car keeps prior config; will retry): {r.get('error')}")
        return "failed"
    except Exception as exc:  # never let an auto-send attempt disrupt the prime loop
        log(f"[autosend] error: {exc!r}")
        return None
    finally:
        _LOCK.release()
