"""FastAPI dependency validating a caller-supplied Home Assistant long-lived access token.

The upload endpoint is reachable from the LAN (not just ingress), so it needs its own auth
independent of Home Assistant's ingress session. We don't mint or store tokens ourselves —
we validate the caller's bearer token by asking Home Assistant Core whether it recognises it.

This calls Core DIRECTLY at http://homeassistant:8123/api/ (the fixed internal hostname/port
Supervisor-managed Core is always reachable at on the add-on Docker network, once
`homeassistant_api: true` grants this container access to it) — NOT the Supervisor's own
`http://supervisor/core/api/` proxy. That proxy authenticates the *add-on itself* to Core
using this container's own SUPERVISOR_TOKEN; it has no way to authenticate an arbitrary
caller-supplied token, and returns 401 for any Authorization header that isn't the add-on's
own SUPERVISOR_TOKEN. Confirmed live (2026-07-17): a genuine long-lived access token got a
clean `200 "API running."` from Core directly at :8123, but 401 through the supervisor proxy.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException, Request

log = logging.getLogger("teslausb_viewer.auth")

_HA_CORE_API = "http://homeassistant:8123/api/"


async def require_ha_token(request: Request) -> None:
    """Raise 401 unless Authorization: Bearer <token> is a token HA Core accepts."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = auth[len("bearer "):].strip()
    if not token:
        raise HTTPException(401, "missing bearer token")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                _HA_CORE_API, headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.TransportError:
        log.warning("Could not reach Home Assistant Core to validate token")
        raise HTTPException(401, "token validation unavailable")
    except Exception:
        log.warning("Token validation failed unexpectedly", exc_info=True)
        raise HTTPException(401, "token validation unavailable")
    if resp.status_code != 200:
        raise HTTPException(401, "invalid token")
