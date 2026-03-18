#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Nginx Proxy Manager MCP add-on..."

# Read config
bashio::log.info "Reading configuration..."
NPM_URL=$(bashio::config 'npm_url')
NPM_EMAIL=$(bashio::config 'npm_email')
NPM_PASSWORD=$(bashio::config 'npm_password')
PORT=$(bashio::config 'port')
SECRET_PATH=$(bashio::config 'secret_path')

# Validate required fields
if bashio::var.is_empty "${NPM_URL}"; then
    bashio::log.fatal "npm_url is not set — configure it in the add-on Configuration tab"
    exit 1
fi
if bashio::var.is_empty "${NPM_EMAIL}"; then
    bashio::log.fatal "npm_email is not set — configure it in the add-on Configuration tab"
    exit 1
fi
if bashio::var.is_empty "${NPM_PASSWORD}"; then
    bashio::log.fatal "npm_password is not set — configure it in the add-on Configuration tab"
    exit 1
fi
if bashio::var.is_empty "${SECRET_PATH}"; then
    bashio::log.fatal "secret_path is not set — configure it in the add-on Configuration tab"
    exit 1
fi
bashio::log.info "Configuration looks good"

# Verify MCP server exists
if [ ! -f "/opt/npm-mcp/server.py" ]; then
    bashio::log.fatal "MCP server not found at /opt/npm-mcp/server.py"
    exit 1
fi
bashio::log.info "MCP server found"

# Verify supergateway is available
if ! command -v supergateway > /dev/null 2>&1; then
    bashio::log.fatal "supergateway not found in PATH"
    exit 1
fi
bashio::log.info "supergateway found at $(command -v supergateway)"

# Log the URL
bashio::log.info "============================================"
bashio::log.info "NPM MCP URL (add to your AI client):"
bashio::log.info "  http://<your-ha-ip>:${PORT}/${SECRET_PATH}/mcp"
bashio::log.info "============================================"

# Export config as env vars for the Python MCP server
export NPM_URL NPM_EMAIL NPM_PASSWORD

bashio::log.info "Starting supergateway on port ${PORT}..."
bashio::log.info "MCP path:     /${SECRET_PATH}/mcp"
bashio::log.info "Health check: /health"

# Start supergateway wrapping the Python MCP server
# Using streamableHttp transport (stateless, handles reconnections gracefully)
exec supergateway \
    --stdio "/opt/npm-mcp/venv/bin/python /opt/npm-mcp/server.py" \
    --outputTransport streamableHttp \
    --port "${PORT}" \
    --streamableHttpPath "/${SECRET_PATH}/mcp" \
    --healthEndpoint "/health"
