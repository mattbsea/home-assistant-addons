"""Nginx Proxy Manager MCP Server.

A Model Context Protocol server that exposes Nginx Proxy Manager
operations as MCP tools via the REST API.
"""

import json
import os
import sys
import time

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

# ---------------------------------------------------------------------------
# NPM API Client
# ---------------------------------------------------------------------------

class NpmClient:
    """Async HTTP client for the Nginx Proxy Manager REST API with JWT auth."""

    def __init__(self, base_url: str, email: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.token: str | None = None
        self.token_expires: float = 0
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=30)

    async def authenticate(self):
        """Obtain a JWT token from the NPM API."""
        resp = await self._http.post(
            "/api/tokens",
            json={"identity": self.email, "secret": self.password},
        )
        resp.raise_for_status()
        data = resp.json()
        self.token = data["token"]
        # Refresh 5 minutes before expiry (tokens last ~1 hour)
        self.token_expires = time.time() + 3300

    async def _ensure_auth(self):
        if self.token is None or time.time() >= self.token_expires:
            await self.authenticate()

    async def _request(self, method: str, path: str, **kwargs) -> dict | list:
        await self._ensure_auth()
        headers = {"Authorization": f"Bearer {self.token}"}
        resp = await self._http.request(method, path, headers=headers, **kwargs)
        # Re-auth once on 401
        if resp.status_code == 401:
            await self.authenticate()
            headers = {"Authorization": f"Bearer {self.token}"}
            resp = await self._http.request(method, path, headers=headers, **kwargs)
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return {"status": "ok"}
        return resp.json()

    async def get(self, path: str, **kwargs):
        return await self._request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs):
        return await self._request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs):
        return await self._request("PUT", path, **kwargs)

    async def delete(self, path: str, **kwargs):
        return await self._request("DELETE", path, **kwargs)


# ---------------------------------------------------------------------------
# Initialise client and MCP server
# ---------------------------------------------------------------------------

npm_url = os.environ.get("NPM_URL", "")
npm_email = os.environ.get("NPM_EMAIL", "")
npm_password = os.environ.get("NPM_PASSWORD", "")

if not all([npm_url, npm_email, npm_password]):
    print("NPM_URL, NPM_EMAIL, and NPM_PASSWORD must be set", file=sys.stderr)
    sys.exit(1)

client = NpmClient(npm_url, npm_email, npm_password)
mcp = FastMCP("nginx-proxy-manager")

# ---------------------------------------------------------------------------
# Tool annotations
# ---------------------------------------------------------------------------

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)


def _json(data) -> str:
    """Serialize API response to indented JSON string."""
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# Proxy Hosts
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
async def list_proxy_hosts() -> str:
    """List all proxy hosts configured in Nginx Proxy Manager."""
    return _json(await client.get("/api/nginx/proxy-hosts"))


@mcp.tool(annotations=READ)
async def get_proxy_host(host_id: int) -> str:
    """Get details of a specific proxy host by ID."""
    return _json(await client.get(f"/api/nginx/proxy-hosts/{host_id}"))


@mcp.tool(annotations=WRITE)
async def create_proxy_host(
    domain_names: list[str],
    forward_scheme: str,
    forward_host: str,
    forward_port: int,
    block_exploits: bool = True,
    allow_websocket_upgrade: bool = False,
    ssl_forced: bool = False,
    certificate_id: int = 0,
    access_list_id: int = 0,
    advanced_config: str = "",
    locations: list[dict] | None = None,
) -> str:
    """Create a new proxy host.

    Args:
        domain_names: List of domain names (e.g. ["example.com", "www.example.com"])
        forward_scheme: Scheme to forward to ("http" or "https")
        forward_host: Hostname or IP to forward to
        forward_port: Port to forward to
        block_exploits: Block common exploits
        allow_websocket_upgrade: Allow WebSocket upgrade
        ssl_forced: Force SSL/HTTPS
        certificate_id: SSL certificate ID (0 for none)
        access_list_id: Access list ID (0 for none)
        advanced_config: Custom Nginx configuration
        locations: List of location overrides
    """
    payload = {
        "domain_names": domain_names,
        "forward_scheme": forward_scheme,
        "forward_host": forward_host,
        "forward_port": forward_port,
        "block_exploits": block_exploits,
        "allow_websocket_upgrade": allow_websocket_upgrade,
        "ssl_forced": ssl_forced,
        "certificate_id": certificate_id,
        "access_list_id": access_list_id,
        "advanced_config": advanced_config,
        "meta": {"letsencrypt_agree": False, "dns_challenge": False},
        "locations": locations or [],
    }
    return _json(await client.post("/api/nginx/proxy-hosts", json=payload))


@mcp.tool(annotations=WRITE)
async def update_proxy_host(
    host_id: int,
    domain_names: list[str],
    forward_scheme: str,
    forward_host: str,
    forward_port: int,
    block_exploits: bool = True,
    allow_websocket_upgrade: bool = False,
    ssl_forced: bool = False,
    certificate_id: int = 0,
    access_list_id: int = 0,
    advanced_config: str = "",
    locations: list[dict] | None = None,
) -> str:
    """Update an existing proxy host.

    Args:
        host_id: ID of the proxy host to update
        domain_names: List of domain names
        forward_scheme: Scheme to forward to ("http" or "https")
        forward_host: Hostname or IP to forward to
        forward_port: Port to forward to
        block_exploits: Block common exploits
        allow_websocket_upgrade: Allow WebSocket upgrade
        ssl_forced: Force SSL/HTTPS
        certificate_id: SSL certificate ID (0 for none)
        access_list_id: Access list ID (0 for none)
        advanced_config: Custom Nginx configuration
        locations: List of location overrides
    """
    payload = {
        "domain_names": domain_names,
        "forward_scheme": forward_scheme,
        "forward_host": forward_host,
        "forward_port": forward_port,
        "block_exploits": block_exploits,
        "allow_websocket_upgrade": allow_websocket_upgrade,
        "ssl_forced": ssl_forced,
        "certificate_id": certificate_id,
        "access_list_id": access_list_id,
        "advanced_config": advanced_config,
        "locations": locations or [],
    }
    return _json(await client.put(f"/api/nginx/proxy-hosts/{host_id}", json=payload))


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_proxy_host(host_id: int) -> str:
    """Delete a proxy host by ID."""
    return _json(await client.delete(f"/api/nginx/proxy-hosts/{host_id}"))


@mcp.tool(annotations=WRITE)
async def enable_proxy_host(host_id: int) -> str:
    """Enable a disabled proxy host."""
    return _json(await client.post(f"/api/nginx/proxy-hosts/{host_id}/enable"))


@mcp.tool(annotations=DESTRUCTIVE)
async def disable_proxy_host(host_id: int) -> str:
    """Disable a proxy host."""
    return _json(await client.post(f"/api/nginx/proxy-hosts/{host_id}/disable"))


# ---------------------------------------------------------------------------
# Redirection Hosts
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
async def list_redirection_hosts() -> str:
    """List all redirection hosts."""
    return _json(await client.get("/api/nginx/redirection-hosts"))


@mcp.tool(annotations=READ)
async def get_redirection_host(host_id: int) -> str:
    """Get details of a specific redirection host by ID."""
    return _json(await client.get(f"/api/nginx/redirection-hosts/{host_id}"))


@mcp.tool(annotations=WRITE)
async def create_redirection_host(
    domain_names: list[str],
    forward_http_code: int,
    forward_scheme: str,
    forward_domain_name: str,
    preserve_path: bool = True,
    block_exploits: bool = True,
    certificate_id: int = 0,
    ssl_forced: bool = False,
    advanced_config: str = "",
) -> str:
    """Create a new redirection host.

    Args:
        domain_names: List of source domain names
        forward_http_code: HTTP redirect code (301, 302, etc.)
        forward_scheme: Scheme to redirect to ("http" or "https" or "$scheme")
        forward_domain_name: Target domain to redirect to
        preserve_path: Preserve the URL path in the redirect
        block_exploits: Block common exploits
        certificate_id: SSL certificate ID (0 for none)
        ssl_forced: Force SSL/HTTPS
        advanced_config: Custom Nginx configuration
    """
    payload = {
        "domain_names": domain_names,
        "forward_http_code": forward_http_code,
        "forward_scheme": forward_scheme,
        "forward_domain_name": forward_domain_name,
        "preserve_path": preserve_path,
        "block_exploits": block_exploits,
        "certificate_id": certificate_id,
        "ssl_forced": ssl_forced,
        "advanced_config": advanced_config,
        "meta": {"letsencrypt_agree": False, "dns_challenge": False},
    }
    return _json(await client.post("/api/nginx/redirection-hosts", json=payload))


@mcp.tool(annotations=WRITE)
async def update_redirection_host(
    host_id: int,
    domain_names: list[str],
    forward_http_code: int,
    forward_scheme: str,
    forward_domain_name: str,
    preserve_path: bool = True,
    block_exploits: bool = True,
    certificate_id: int = 0,
    ssl_forced: bool = False,
    advanced_config: str = "",
) -> str:
    """Update an existing redirection host.

    Args:
        host_id: ID of the redirection host to update
        domain_names: List of source domain names
        forward_http_code: HTTP redirect code (301, 302, etc.)
        forward_scheme: Scheme to redirect to
        forward_domain_name: Target domain to redirect to
        preserve_path: Preserve the URL path in the redirect
        block_exploits: Block common exploits
        certificate_id: SSL certificate ID (0 for none)
        ssl_forced: Force SSL/HTTPS
        advanced_config: Custom Nginx configuration
    """
    payload = {
        "domain_names": domain_names,
        "forward_http_code": forward_http_code,
        "forward_scheme": forward_scheme,
        "forward_domain_name": forward_domain_name,
        "preserve_path": preserve_path,
        "block_exploits": block_exploits,
        "certificate_id": certificate_id,
        "ssl_forced": ssl_forced,
        "advanced_config": advanced_config,
    }
    return _json(await client.put(f"/api/nginx/redirection-hosts/{host_id}", json=payload))


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_redirection_host(host_id: int) -> str:
    """Delete a redirection host by ID."""
    return _json(await client.delete(f"/api/nginx/redirection-hosts/{host_id}"))


@mcp.tool(annotations=WRITE)
async def enable_redirection_host(host_id: int) -> str:
    """Enable a disabled redirection host."""
    return _json(await client.post(f"/api/nginx/redirection-hosts/{host_id}/enable"))


@mcp.tool(annotations=DESTRUCTIVE)
async def disable_redirection_host(host_id: int) -> str:
    """Disable a redirection host."""
    return _json(await client.post(f"/api/nginx/redirection-hosts/{host_id}/disable"))


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
async def list_streams() -> str:
    """List all TCP/UDP stream proxies."""
    return _json(await client.get("/api/nginx/streams"))


@mcp.tool(annotations=READ)
async def get_stream(stream_id: int) -> str:
    """Get details of a specific stream by ID."""
    return _json(await client.get(f"/api/nginx/streams/{stream_id}"))


@mcp.tool(annotations=WRITE)
async def create_stream(
    incoming_port: int,
    forwarding_host: str,
    forwarding_port: int,
    tcp_forwarding: bool = True,
    udp_forwarding: bool = False,
) -> str:
    """Create a new TCP/UDP stream proxy.

    Args:
        incoming_port: Port to listen on
        forwarding_host: Hostname or IP to forward to
        forwarding_port: Port to forward to
        tcp_forwarding: Enable TCP forwarding
        udp_forwarding: Enable UDP forwarding
    """
    payload = {
        "incoming_port": incoming_port,
        "forwarding_host": forwarding_host,
        "forwarding_port": forwarding_port,
        "tcp_forwarding": tcp_forwarding,
        "udp_forwarding": udp_forwarding,
        "meta": {},
    }
    return _json(await client.post("/api/nginx/streams", json=payload))


@mcp.tool(annotations=WRITE)
async def update_stream(
    stream_id: int,
    incoming_port: int,
    forwarding_host: str,
    forwarding_port: int,
    tcp_forwarding: bool = True,
    udp_forwarding: bool = False,
) -> str:
    """Update an existing stream proxy.

    Args:
        stream_id: ID of the stream to update
        incoming_port: Port to listen on
        forwarding_host: Hostname or IP to forward to
        forwarding_port: Port to forward to
        tcp_forwarding: Enable TCP forwarding
        udp_forwarding: Enable UDP forwarding
    """
    payload = {
        "incoming_port": incoming_port,
        "forwarding_host": forwarding_host,
        "forwarding_port": forwarding_port,
        "tcp_forwarding": tcp_forwarding,
        "udp_forwarding": udp_forwarding,
    }
    return _json(await client.put(f"/api/nginx/streams/{stream_id}", json=payload))


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_stream(stream_id: int) -> str:
    """Delete a stream proxy by ID."""
    return _json(await client.delete(f"/api/nginx/streams/{stream_id}"))


@mcp.tool(annotations=WRITE)
async def enable_stream(stream_id: int) -> str:
    """Enable a disabled stream proxy."""
    return _json(await client.post(f"/api/nginx/streams/{stream_id}/enable"))


@mcp.tool(annotations=DESTRUCTIVE)
async def disable_stream(stream_id: int) -> str:
    """Disable a stream proxy."""
    return _json(await client.post(f"/api/nginx/streams/{stream_id}/disable"))


# ---------------------------------------------------------------------------
# Dead Hosts (404 hosts)
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
async def list_dead_hosts() -> str:
    """List all dead hosts (custom 404 pages)."""
    return _json(await client.get("/api/nginx/dead-hosts"))


@mcp.tool(annotations=READ)
async def get_dead_host(host_id: int) -> str:
    """Get details of a specific dead host by ID."""
    return _json(await client.get(f"/api/nginx/dead-hosts/{host_id}"))


@mcp.tool(annotations=WRITE)
async def create_dead_host(
    domain_names: list[str],
    certificate_id: int = 0,
    ssl_forced: bool = False,
    advanced_config: str = "",
) -> str:
    """Create a new dead host (custom 404 page).

    Args:
        domain_names: List of domain names
        certificate_id: SSL certificate ID (0 for none)
        ssl_forced: Force SSL/HTTPS
        advanced_config: Custom Nginx configuration
    """
    payload = {
        "domain_names": domain_names,
        "certificate_id": certificate_id,
        "ssl_forced": ssl_forced,
        "advanced_config": advanced_config,
        "meta": {"letsencrypt_agree": False, "dns_challenge": False},
    }
    return _json(await client.post("/api/nginx/dead-hosts", json=payload))


@mcp.tool(annotations=WRITE)
async def update_dead_host(
    host_id: int,
    domain_names: list[str],
    certificate_id: int = 0,
    ssl_forced: bool = False,
    advanced_config: str = "",
) -> str:
    """Update an existing dead host.

    Args:
        host_id: ID of the dead host to update
        domain_names: List of domain names
        certificate_id: SSL certificate ID (0 for none)
        ssl_forced: Force SSL/HTTPS
        advanced_config: Custom Nginx configuration
    """
    payload = {
        "domain_names": domain_names,
        "certificate_id": certificate_id,
        "ssl_forced": ssl_forced,
        "advanced_config": advanced_config,
    }
    return _json(await client.put(f"/api/nginx/dead-hosts/{host_id}", json=payload))


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_dead_host(host_id: int) -> str:
    """Delete a dead host by ID."""
    return _json(await client.delete(f"/api/nginx/dead-hosts/{host_id}"))


@mcp.tool(annotations=WRITE)
async def enable_dead_host(host_id: int) -> str:
    """Enable a disabled dead host."""
    return _json(await client.post(f"/api/nginx/dead-hosts/{host_id}/enable"))


@mcp.tool(annotations=DESTRUCTIVE)
async def disable_dead_host(host_id: int) -> str:
    """Disable a dead host."""
    return _json(await client.post(f"/api/nginx/dead-hosts/{host_id}/disable"))


# ---------------------------------------------------------------------------
# SSL Certificates
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
async def list_certificates() -> str:
    """List all SSL certificates."""
    return _json(await client.get("/api/nginx/certificates"))


@mcp.tool(annotations=READ)
async def get_certificate(cert_id: int) -> str:
    """Get details of a specific SSL certificate by ID."""
    return _json(await client.get(f"/api/nginx/certificates/{cert_id}"))


@mcp.tool(annotations=WRITE)
async def create_certificate(
    domain_names: list[str],
    provider: str = "letsencrypt",
    letsencrypt_email: str = "",
    dns_challenge: bool = False,
    dns_provider: str = "",
    dns_provider_credentials: str = "",
) -> str:
    """Create/request a new SSL certificate.

    Args:
        domain_names: List of domain names for the certificate
        provider: Certificate provider ("letsencrypt" or "other")
        letsencrypt_email: Email for Let's Encrypt notifications
        dns_challenge: Use DNS challenge instead of HTTP
        dns_provider: DNS provider for DNS challenge (e.g. "cloudflare")
        dns_provider_credentials: Credentials for DNS provider
    """
    payload: dict = {
        "domain_names": domain_names,
        "provider": provider,
        "meta": {
            "letsencrypt_agree": True,
            "letsencrypt_email": letsencrypt_email,
            "dns_challenge": dns_challenge,
        },
    }
    if dns_challenge and dns_provider:
        payload["meta"]["dns_provider"] = dns_provider
        payload["meta"]["dns_provider_credentials"] = dns_provider_credentials
    return _json(await client.post("/api/nginx/certificates", json=payload))


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_certificate(cert_id: int) -> str:
    """Delete an SSL certificate by ID."""
    return _json(await client.delete(f"/api/nginx/certificates/{cert_id}"))


@mcp.tool(annotations=WRITE)
async def renew_certificate(cert_id: int) -> str:
    """Renew a Let's Encrypt certificate."""
    return _json(await client.post(f"/api/nginx/certificates/{cert_id}/renew"))


# ---------------------------------------------------------------------------
# Access Lists
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
async def list_access_lists() -> str:
    """List all access lists."""
    return _json(await client.get("/api/nginx/access-lists"))


@mcp.tool(annotations=READ)
async def get_access_list(list_id: int) -> str:
    """Get details of a specific access list by ID."""
    return _json(await client.get(f"/api/nginx/access-lists/{list_id}"))


@mcp.tool(annotations=WRITE)
async def create_access_list(
    name: str,
    satisfy_any: bool = False,
    pass_auth: bool = False,
    items: list[dict] | None = None,
    clients: list[dict] | None = None,
) -> str:
    """Create a new access list.

    Args:
        name: Name of the access list
        satisfy_any: If true, satisfy any rule (OR logic). If false, satisfy all (AND logic).
        pass_auth: Pass basic auth to upstream
        items: List of auth items [{"username": "...", "password": "..."}]
        clients: List of client rules [{"address": "...", "directive": "allow|deny"}]
    """
    payload = {
        "name": name,
        "satisfy_any": satisfy_any,
        "pass_auth": pass_auth,
        "items": items or [],
        "clients": clients or [],
        "meta": {},
    }
    return _json(await client.post("/api/nginx/access-lists", json=payload))


@mcp.tool(annotations=WRITE)
async def update_access_list(
    list_id: int,
    name: str,
    satisfy_any: bool = False,
    pass_auth: bool = False,
    items: list[dict] | None = None,
    clients: list[dict] | None = None,
) -> str:
    """Update an existing access list.

    Args:
        list_id: ID of the access list to update
        name: Name of the access list
        satisfy_any: If true, satisfy any rule (OR logic)
        pass_auth: Pass basic auth to upstream
        items: List of auth items [{"username": "...", "password": "..."}]
        clients: List of client rules [{"address": "...", "directive": "allow|deny"}]
    """
    payload = {
        "name": name,
        "satisfy_any": satisfy_any,
        "pass_auth": pass_auth,
        "items": items or [],
        "clients": clients or [],
    }
    return _json(await client.put(f"/api/nginx/access-lists/{list_id}", json=payload))


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_access_list(list_id: int) -> str:
    """Delete an access list by ID."""
    return _json(await client.delete(f"/api/nginx/access-lists/{list_id}"))


# ---------------------------------------------------------------------------
# Settings (read-only)
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
async def get_settings() -> str:
    """Get all Nginx Proxy Manager settings."""
    return _json(await client.get("/api/settings"))


# ---------------------------------------------------------------------------
# Audit Log (read-only)
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
async def get_audit_log() -> str:
    """Get the audit log of all operations."""
    return _json(await client.get("/api/audit-log"))


# ---------------------------------------------------------------------------
# Reports (read-only)
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
async def get_host_report() -> str:
    """Get a summary report of all hosts."""
    return _json(await client.get("/api/reports/hosts"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
