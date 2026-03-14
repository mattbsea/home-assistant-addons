#!/usr/bin/with-contenv bashio

# Read config
PORTAINER_URL=$(bashio::config 'portainer_url')
PORTAINER_TOKEN=$(bashio::config 'portainer_token')

# Validate
if bashio::var.is_empty "${PORTAINER_URL}"; then
    bashio::log.fatal "portainer_url is required"
    exit 1
fi
if bashio::var.is_empty "${PORTAINER_TOKEN}"; then
    bashio::log.fatal "portainer_token is required"
    exit 1
fi

# Strip http(s):// scheme — portainer-mcp expects host:port
PORTAINER_HOST=$(echo "${PORTAINER_URL}" | sed 's|https\?://||' | sed 's|/$||')

# Generate or load secret path
SECRET_FILE="/data/secret_path.txt"
if [ ! -f "${SECRET_FILE}" ]; then
    SECRET=$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 32)
    echo "${SECRET}" > "${SECRET_FILE}"
fi
SECRET=$(cat "${SECRET_FILE}")
SECRET_PATH="private_${SECRET}"

# Log the URL
bashio::log.info "============================================"
bashio::log.info "Portainer MCP URL (add to your AI client):"
bashio::log.info "  http://<your-ha-ip>:9584/${SECRET_PATH}/sse"
bashio::log.info "============================================"

# Start supergateway wrapping portainer-mcp
exec supergateway \
    --stdio "/usr/local/bin/portainer-mcp -server ${PORTAINER_HOST} -token ${PORTAINER_TOKEN} -tools /data/tools.yaml -disable-version-check" \
    --port 9584 \
    --ssePath "/${SECRET_PATH}/sse" \
    --messagePath "/${SECRET_PATH}/message" \
    --healthEndpoint "/health"
