"""Fleet-API cold-start priming for the unified app.

Ported from the v0 shim's prime_once: refresh the OAuth token, list products, and for each
already-online vehicle fetch a full vehicle_data snapshot and hand it to the Registry as a prime
(so cold-start dashboards/TeslaMate see a complete picture before the live stream fills in). We
never wake a sleeping car. HTTP is injectable so the orchestration is unit-testable.
"""
import json
import urllib.parse
import urllib.request

_VEHICLE_DATA_ENDPOINTS = "charge_state;climate_state;drive_state;location_data;vehicle_config;vehicle_state;gui_settings"
_PRIME_SECTIONS = ("drive_state", "charge_state", "climate_state", "vehicle_state", "vehicle_config")


def _post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def extract_prime(vehicle_data_response):
    """Pull just the prime sections from a vehicle_data response."""
    return {k: vehicle_data_response.get(k) for k in _PRIME_SECTIONS}


def prime_once(registry, *, client_id, refresh_token, auth_host, fleet_host,
               post_form=_post_form, get=_get, on_token=None, log=print):
    """Refresh the token, list products, and prime every already-online vehicle. Returns the count
    of vehicles primed. `on_token(new_refresh_token)` is called when Tesla rotates it."""
    if not (client_id and refresh_token):
        log("[prime] disabled (no client_id/refresh_token)")
        return 0
    try:
        tok = post_form(auth_host + "/oauth2/v3/token",
                        {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token})
    except Exception as e:
        log(f"[prime] token refresh failed: {e}")
        return 0
    new_rt = tok.get("refresh_token")
    if new_rt and new_rt != refresh_token and on_token:
        on_token(new_rt)
    at = tok.get("access_token")
    if not at:
        log("[prime] no access_token returned")
        return 0
    try:
        products = get(fleet_host + "/api/1/products", at).get("response", []) or []
    except Exception as e:
        log(f"[prime] /products failed: {e}")
        return 0
    primed = 0
    for p in products:
        vin = p.get("vin")
        tid = p.get("id")
        if not vin or "vehicle_id" not in p or not tid:
            continue
        if p.get("state") != "online":
            log(f"[prime] {vin} is {p.get('state')} — skipping vehicle_data")
            continue
        try:
            url = fleet_host + f"/api/1/vehicles/{tid}/vehicle_data?endpoints=" + urllib.parse.quote(_VEHICLE_DATA_ENDPOINTS)
            vd = get(url, at).get("response", {})
        except Exception as e:
            log(f"[prime] vehicle_data({vin}) failed: {e}")
            continue
        registry.set_prime(vin, extract_prime(vd), tesla_id=tid, display_name=p.get("display_name"))
        primed += 1
    log(f"[prime] primed {primed} online vehicle(s) from Tesla")
    return primed
