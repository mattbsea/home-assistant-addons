"""Auto re-send fleet_telemetry_config when the requested-field roster changes.

The roster (fields.TELEMETRY_FIELDS) defines what the car streams, but it only takes effect after a
signed fleet_telemetry_config command. So when the add-on upgrades and the roster changes, the new
fields would otherwise sit idle until the user manually clicked "Send to Vehicle".

This compares a fingerprint of the roster against the last successfully-sent one (kept in
shim-state.json — the UNWATCHED file; never wizard-config.json, which would bounce the binary) and
re-sends when they differ. Safety: a rejected send is inert — Tesla validates the config atomically
and the car keeps streaming its existing config — so a bad roster field can't drop telemetry; the
failed send is logged and retried on the next cycle (the hash is only stored on success).
"""
import os

import fields
from app.control import config as cfgmod, sendconfig, tokens


def maybe_resend(*, vins, config_path, shim_state_path, wizard_state_path, cert_file, private_key_path,
                 auth_host="https://auth.tesla.com", log=print):
    """Re-send the telemetry config iff setup is complete and the roster fingerprint changed.
    Returns "sent" / "failed" / None (no-op). Never raises."""
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
        roster = fields.effective_roster(state.get("telemetry_roster"))
        cur = fields.telemetry_fields_hash(roster)
        if state.get("telemetry_fields_hash") == cur:
            return None  # roster unchanged
        try:
            port = int(c.get("telemetry_port") or 4443)
        except (TypeError, ValueError):
            port = 4443
        rt = state.get("refresh_token") or c.get("shim_refresh_token", "")
        log(f"[autosend] telemetry roster changed — re-sending fleet_telemetry_config to {len(vins)} vehicle(s)")
        r = sendconfig.send(vins=vins, client_id=c.get("client_id", ""), refresh_token=rt,
                            domain=domain, region=c.get("region", "na"), port=port,
                            cert_file=cert_file, private_key_file=private_key_path, auth_host=auth_host,
                            roster=roster)
        # Persist any rotated token regardless of outcome (it was rotated during the token refresh).
        if r.get("new_refresh_token"):
            tokens.save(shim_state_path, r["new_refresh_token"])
        if r.get("ok"):
            tokens.write_state(shim_state_path, telemetry_fields_hash=cur)  # only mark sent on success
            log(f"[autosend] roster re-sent OK: {r.get('response')}")
            return "sent"
        log(f"[autosend] roster re-send FAILED (inert — car keeps prior config; will retry): {r.get('error')}")
        return "failed"
    except Exception as exc:  # never let an auto-send attempt disrupt the prime loop
        log(f"[autosend] error: {exc!r}")
        return None
