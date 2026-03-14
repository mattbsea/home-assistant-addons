# Portainer MCP Add-on Documentation

Exposes the [Portainer MCP server](https://github.com/portainer/portainer-mcp) over HTTP/SSE with a secret-path URL, allowing AI clients (Claude Desktop, Cursor, etc.) to manage Docker containers and stacks through Portainer.

## Architecture

```
MCP Client → port 9584 → supergateway (SSE/HTTP) → portainer-mcp (stdio)
```

## Configuration Options

### `portainer_url` (required)

The URL or host:port of your Portainer instance.

Examples:
- `http://192.168.1.100:9000`
- `192.168.1.100:9000`
- `https://portainer.example.com`

Note: The `http://` or `https://` prefix is stripped automatically; portainer-mcp expects `host:port` format.

### `portainer_token` (required)

Your Portainer admin API token.

To generate one:
1. Log into Portainer → click your username (top-right) → **My account**
2. Scroll to **Access tokens** → **Add access token**
3. Give it a name (e.g. `home-assistant-mcp`) and copy the token

## Finding Your MCP URL

The secret URL is generated once on first start and persisted across restarts. Check the add-on logs after starting:

```
============================================
Portainer MCP URL (add to your AI client):
  http://<your-ha-ip>:9584/private_<32-hex-chars>/mcp
============================================
```

Replace `<your-ha-ip>` with your Home Assistant IP address.

## AI Client Configuration

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "portainer": {
      "url": "http://192.168.1.10:9584/private_abc123.../mcp"
    }
  }
}
```

### Other MCP clients

Use the SSE URL from the logs. Most clients that support SSE transport will work.

A `/health` endpoint is also available at `http://<ha-ip>:9584/health` for liveness checks.

## Security

- The URL contains a 128-bit random secret token — treat it like a password
- Anyone with the URL can control your Portainer instance
- Port 9584 must be reachable from your AI client (open firewall if needed)
- The secret persists in `/data/secret_path.txt`; delete this file and restart to rotate it

## Troubleshooting

- **Add-on fails to start**: Ensure `portainer_url` and `portainer_token` are set
- **MCP client can't connect**: Verify port 9584 is accessible and the full SSE URL is correct
- **Authentication errors**: Regenerate the Portainer API token and update the add-on config
