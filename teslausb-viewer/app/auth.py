"""FastAPI dependency validating a caller-supplied Home Assistant long-lived access token.

The upload endpoint is reachable from the LAN (not just ingress), so it needs its own auth
independent of Home Assistant's ingress session. We don't mint or store tokens ourselves —
we validate the caller's bearer token by asking Home Assistant Core whether it recognises it,
via the Supervisor's core API proxy (this add-on has `homeassistant_api: true`, so Supervisor
routes http://supervisor/core/... through to HA Core using the caller's own token).
"""

from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException, Request

log = logging.getLogger("teslausb_viewer.auth")

_SUPERVISOR_CORE_API = "http://supervisor/core/api/"


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
                _SUPERVISOR_CORE_API, headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.TransportError:
        log.warning("Could not reach Supervisor to validate token")
        raise HTTPException(401, "token validation unavailable")
    if resp.status_code != 200:
        raise HTTPException(401, "invalid token")
