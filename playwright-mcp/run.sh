#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Playwright MCP add-on..."

PORT=9585
BROWSER=$(bashio::config 'browser')
VIEWPORT=$(bashio::config 'viewport_size')
EXTRA_ARGS=$(bashio::config 'extra_args')
BROWSER=${BROWSER:-chromium}
VIEWPORT=${VIEWPORT:-1280x720}

if [ ! -x "/opt/playwright-mcp/node_modules/.bin/playwright-mcp" ]; then
    bashio::log.fatal "playwright-mcp not found in /opt/playwright-mcp"
    exit 1
fi
if ! command -v supergateway > /dev/null 2>&1; then
    bashio::log.fatal "supergateway not found in PATH"
    exit 1
fi

# Secret path: use config option if set, otherwise generate/load from /data
SECRET_FILE="/data/secret_path.txt"
SECRET_PATH=""

if bashio::config.has_value 'secret_path'; then
    SECRET_PATH=$(bashio::config 'secret_path')
    bashio::log.info "Using secret path from add-on configuration"
fi

if bashio::var.is_empty "${SECRET_PATH}"; then
    if [ ! -f "${SECRET_FILE}" ]; then
        bashio::log.info "Generating new secret path..."
        echo "private_$(openssl rand -hex 16)" > "${SECRET_FILE}"
        chmod 600 "${SECRET_FILE}"
    else
        bashio::log.info "Loading existing secret path from ${SECRET_FILE}"
    fi
    SECRET_PATH=$(cat "${SECRET_FILE}")
fi

# Chromium in a root container needs --no-sandbox; /dev/shm is small in
# Docker, so keep shared memory off it.
CONFIG_FILE="/tmp/playwright-mcp-config.json"
cat > "${CONFIG_FILE}" <<JSON
{
  "browser": {
    "browserName": "${BROWSER}",
    "isolated": true,
    "launchOptions": {
      "headless": true,
      "chromiumSandbox": false,
      "args": ["--no-sandbox", "--disable-dev-shm-usage"]
    },
    "contextOptions": {
      "viewport": { "width": ${VIEWPORT%x*}, "height": ${VIEWPORT#*x} }
    }
  }
}
JSON

bashio::log.info "============================================"
bashio::log.info "Playwright MCP URL (add to your AI client):"
bashio::log.info "  http://<your-ha-ip>:${PORT}/${SECRET_PATH}/mcp"
bashio::log.info "============================================"
bashio::log.info "Browser: ${BROWSER}, viewport: ${VIEWPORT}"

# --stateful + --sessionTimeout: one playwright-mcp (and browser) per
# Mcp-Session-Id, reaped after 30 min idle. Stateless mode leaks a child
# process per request (see portainer-mcp 0.1.6).
# crash-guard.cjs: supergateway dies on a client dropping mid-initialize
# (claude.ai's connect probe does this); see CHANGELOG 0.1.1.
# shellcheck disable=SC2086
exec env NODE_OPTIONS="--require /opt/crash-guard.cjs" supergateway \
    --stdio "/opt/playwright-mcp/node_modules/.bin/playwright-mcp --config ${CONFIG_FILE} ${EXTRA_ARGS}" \
    --outputTransport streamableHttp \
    --stateful \
    --sessionTimeout 1800000 \
    --port ${PORT} \
    --streamableHttpPath "/${SECRET_PATH}/mcp" \
    --healthEndpoint "/health"
