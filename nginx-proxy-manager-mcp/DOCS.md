# Nginx Proxy Manager MCP Add-on Documentation

Exposes [Nginx Proxy Manager](https://nginxproxymanager.com) to AI clients (Claude Desktop, Cursor, etc.) via MCP over HTTP, allowing management of proxy hosts, redirections, streams, SSL certificates, and access lists.

## Architecture

```
MCP Client → port 9565 → supergateway (streamableHttp) → npm_mcp_server.py (stdio) → NPM REST API
```

## Configuration Options

### `npm_url` (required)

The full URL of your Nginx Proxy Manager instance.

Examples:
- `http://192.168.1.100:81`
- `https://npm.example.com`

### `npm_email` (required)

The email address of an NPM admin user.

### `npm_password` (required)

The password for the NPM admin user.

## Network

The default port is **9565**. You can change it in the **Network** section of the add-on configuration page.

### `secret_path` (optional)

The secret string used in the MCP URL path for security. If left empty, a 128-bit random secret is automatically generated on first start and persisted across restarts and updates. You can copy the generated value from the logs into this field to make it visible, or set your own custom value.

## Finding Your MCP URL

After starting the add-on, check the logs:

```
============================================
NPM MCP URL (add to your AI client):
  http://<your-ha-ip>:9565/private_<32-hex-chars>/mcp
============================================
```

Replace `<your-ha-ip>` with your Home Assistant IP address.

## AI Client Configuration

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "nginx-proxy-manager": {
      "url": "http://192.168.1.10:9565/my-secret-path/mcp"
    }
  }
}
```

### Other MCP clients

Use the streamableHttp URL from the logs. Most MCP clients that support HTTP transport will work.

A `/health` endpoint is available at `http://<ha-ip>:9565/health` for liveness checks.

## Available Tools

### Proxy Hosts
- `list_proxy_hosts` - List all proxy hosts
- `get_proxy_host` - Get a specific proxy host
- `create_proxy_host` - Create a new proxy host
- `update_proxy_host` - Update a proxy host
- `delete_proxy_host` - Delete a proxy host
- `enable_proxy_host` / `disable_proxy_host` - Toggle proxy host

### Redirection Hosts
- `list_redirection_hosts`, `get_redirection_host`, `create_redirection_host`, `update_redirection_host`, `delete_redirection_host`, `enable_redirection_host`, `disable_redirection_host`

### Streams (TCP/UDP)
- `list_streams`, `get_stream`, `create_stream`, `update_stream`, `delete_stream`, `enable_stream`, `disable_stream`

### Dead Hosts (404 pages)
- `list_dead_hosts`, `get_dead_host`, `create_dead_host`, `update_dead_host`, `delete_dead_host`, `enable_dead_host`, `disable_dead_host`

### SSL Certificates
- `list_certificates`, `get_certificate`, `create_certificate`, `delete_certificate`, `renew_certificate`

### Access Lists
- `list_access_lists`, `get_access_list`, `create_access_list`, `update_access_list`, `delete_access_list`

### Read-only
- `get_settings` - NPM settings
- `get_audit_log` - Audit log
- `get_host_report` - Host summary report

## Security

- The URL contains your secret path — treat it like a password
- Anyone with the URL can manage your Nginx Proxy Manager instance
- The configured port must be reachable from your AI client
- NPM credentials are passed via environment variables (not on the command line)

## Troubleshooting

- **Add-on fails to start**: Ensure `npm_url`, `npm_email`, and `npm_password` are all set
- **MCP client can't connect**: Verify the port is accessible and the full URL is correct
- **Authentication errors**: Verify the NPM email and password are correct and the user has admin privileges
- **Token expired**: The server automatically refreshes JWT tokens; if issues persist, restart the add-on
