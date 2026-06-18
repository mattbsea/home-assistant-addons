"""Tesla Fleet-API OAuth + partner registration (control plane).

Ported from the v0 server as pure orchestration with injectable HTTP. The route layer persists the
rotated refresh token / partner_registered flag to config; these functions just talk to Tesla.
"""
import urllib.parse

AUTH_HOST_DEFAULT = "https://auth.tesla.com"
OAUTH_SCOPES = "openid offline_access vehicle_device_data vehicle_location"
PARTNER_SCOPES = "openid vehicle_device_data vehicle_location"
FLEET_HOSTS = {
    "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",
    "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
    "cn": "https://fleet-api.prd.cn.vn.cloud.tesla.com",
}


def fleet_host(region):
    return FLEET_HOSTS.get((region or "na").lower(), FLEET_HOSTS["na"])


def authorize_url(client_id, redirect_uri, state, auth_host=AUTH_HOST_DEFAULT):
    return auth_host + "/oauth2/v3/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "scope": OAUTH_SCOPES, "state": state,
    })


def exchange_code(*, client_id, client_secret, code, redirect_uri, region,
                  form_post, auth_host=AUTH_HOST_DEFAULT):
    """Exchange an auth code for tokens; returns {ok, refresh_token} or {ok: False, error}."""
    if not client_id or not client_secret:
        return {"ok": False, "error": "Tesla client_id and client_secret must be set (step 3)."}
    if not code:
        return {"ok": False, "error": "Authorization code is required."}
    status, body = form_post(auth_host + "/oauth2/v3/token", {
        "grant_type": "authorization_code", "client_id": client_id, "client_secret": client_secret,
        "code": code, "redirect_uri": redirect_uri, "audience": fleet_host(region),
    })
    if not isinstance(body, dict) or not body.get("refresh_token"):
        return {"ok": False, "error": f"Token exchange failed (HTTP {status}): {str(body)[:300]}"}
    return {"ok": True, "refresh_token": body["refresh_token"]}


def register_partner(*, client_id, client_secret, domain, region, form_post, http_json,
                     auth_host=AUTH_HOST_DEFAULT):
    """Register the public-key domain as a partner account; returns {ok} or {ok: False, error}."""
    if not client_id or not client_secret:
        return {"ok": False, "error": "Tesla client_id and client_secret must be set (step 3)."}
    domain = (domain or "").strip().lower()
    if not domain:
        return {"ok": False, "error": "Set your public-key domain first."}
    host = fleet_host(region)
    status, tok = form_post(auth_host + "/oauth2/v3/token", {
        "grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret,
        "scope": PARTNER_SCOPES, "audience": host,
    })
    if not isinstance(tok, dict) or not tok.get("access_token"):
        return {"ok": False, "error": f"Partner token request failed (HTTP {status}): {str(tok)[:300]}"}
    rstatus, rbody = http_json("POST", host + "/api/1/partner_accounts",
                               headers={"Authorization": "Bearer " + tok["access_token"]},
                               data={"domain": domain})
    if rstatus not in (200, 201, 204):
        return {"ok": False, "error": f"Domain registration failed (HTTP {rstatus}): {str(rbody)[:300]}"}
    return {"ok": True, "response": rbody}
