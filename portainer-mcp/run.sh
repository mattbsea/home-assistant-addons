#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Portainer MCP add-on..."

# Read config
bashio::log.info "Reading configuration..."
PORTAINER_URL=$(bashio::config 'portainer_url')
PORTAINER_TOKEN=$(bashio::config 'portainer_token')

# Validate
if bashio::var.is_empty "${PORTAINER_URL}"; then
    bashio::log.fatal "portainer_url is not set — configure it in the add-on Configuration tab"
    exit 1
fi
if bashio::var.is_empty "${PORTAINER_TOKEN}"; then
    bashio::log.fatal "portainer_token is not set — configure it in the add-on Configuration tab"
    exit 1
fi
bashio::log.info "Configuration looks good"

# Strip http(s):// scheme — portainer-mcp expects host:port
PORTAINER_HOST=$(echo "${PORTAINER_URL}" | sed 's|https\?://||' | sed 's|/$||')
bashio::log.info "Portainer host: ${PORTAINER_HOST}"

# Verify portainer-mcp binary exists
if [ ! -x "/usr/local/bin/portainer-mcp" ]; then
    bashio::log.fatal "portainer-mcp binary not found at /usr/local/bin/portainer-mcp"
    exit 1
fi
bashio::log.info "portainer-mcp binary found"

# Verify supergateway is available
if ! command -v supergateway > /dev/null 2>&1; then
    bashio::log.fatal "supergateway not found in PATH"
    exit 1
fi
bashio::log.info "supergateway found at $(command -v supergateway)"

# Generate or load secret path
SECRET_FILE="/data/secret_path.txt"
if [ ! -f "${SECRET_FILE}" ]; then
    bashio::log.info "Generating new secret path..."
    SECRET=$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 32)
    echo "${SECRET}" > "${SECRET_FILE}"
    bashio::log.info "Secret path generated and saved to ${SECRET_FILE}"
else
    bashio::log.info "Loading existing secret path from ${SECRET_FILE}"
fi
SECRET=$(cat "${SECRET_FILE}")
SECRET_PATH="private_${SECRET}"

# Log the URL
bashio::log.info "============================================"
bashio::log.info "Portainer MCP URL (add to your AI client):"
bashio::log.info "  http://<your-ha-ip>:9584/${SECRET_PATH}/sse"
bashio::log.info "============================================"

bashio::log.info "Starting supergateway on port 9584..."
bashio::log.info "SSE path:     /${SECRET_PATH}/sse"
bashio::log.info "Message path: /${SECRET_PATH}/message"
bashio::log.info "Health check: /health"

# Start supergateway wrapping portainer-mcp
exec supergateway \
    --stdio "/usr/local/bin/portainer-mcp -server ${PORTAINER_HOST} -token ${PORTAINER_TOKEN} -tools /data/tools.yaml -disable-version-check" \
    --port 9584 \
    --ssePath "/${SECRET_PATH}/sse" \
    --messagePath "/${SECRET_PATH}/message" \
    --healthEndpoint "/health"
