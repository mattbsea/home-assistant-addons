# Playwright MCP Add-on Documentation

Runs [Playwright MCP](https://github.com/microsoft/playwright-mcp) (a headless browser) and exposes it over HTTP with a secret-path URL, so AI clients can browse, click, fill forms, take screenshots and read pages without any password.

## Architecture

```
MCP Client → port 9585 → supergateway (streamable HTTP) → playwright-mcp (stdio) → headless browser
```

One `playwright-mcp` process and browser is started per MCP session and reaped after 30 minutes idle. Each session uses an isolated, in-memory browser profile, so nothing is persisted between sessions.

## Configuration Options

### `secret_path` (optional)

Overrides the URL path. Leave empty to auto-generate `private_<32 hex chars>` on first start; it is saved in `/data/secret_path.txt` and survives restarts and updates.

### `browser` (default `chromium`)

`chromium`, `firefox` or `webkit`.

### `viewport_size` (default `1280x720`)

Browser window size, `WIDTHxHEIGHT`.

### `extra_args` (optional)

Extra command-line flags for `playwright-mcp`, e.g. `--caps vision,pdf` or `--user-agent "..."`. See the [Playwright MCP docs](https://github.com/microsoft/playwright-mcp#configuration) for the full list.

## Finding Your MCP URL

Check the add-on log after starting:

```
Playwright MCP URL (add to your AI client):
  http://<your-ha-ip>:9585/private_<32-hex-chars>/mcp
```

Health check: `http://<ha-ip>:9585/health`.

## AI Client Configuration

Claude Code:

```bash
claude mcp add --transport http playwright http://192.168.1.10:9585/private_abc123.../mcp
```

Claude Desktop / Cursor (`mcpServers`):

```json
{
  "mcpServers": {
    "playwright": {
      "url": "http://192.168.1.10:9585/private_abc123.../mcp"
    }
  }
}
```

For access from outside your network, put an HTTPS reverse proxy (e.g. Nginx Proxy Manager) in front of port 9585 and use `https://<your-domain>/private_.../mcp`. Disable response buffering on that proxy host.

## Security

- The URL contains a 128-bit random secret; treat it like a password.
- Anyone with the URL can drive a browser **inside your network**, including reaching LAN-only pages (Home Assistant, routers, etc.). Do not expose it publicly without HTTPS, and rotate it if leaked.
- To rotate: delete `/data/secret_path.txt` (or change `secret_path`) and restart the add-on.

## Troubleshooting

- **Browser crashes / blank pages**: the add-on already passes `--no-sandbox --disable-dev-shm-usage`; check available RAM (Chromium needs a few hundred MB per session).
- **Client can't connect**: verify port 9585 is reachable and the full URL, including `/mcp`, is used.
- **First build is slow**: the image downloads three browsers (~1 GB). Rebuilds are cached.
