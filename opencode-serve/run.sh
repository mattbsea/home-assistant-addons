#!/usr/bin/env bash

echo "=== OpenCode HA Add-on Starting ==="

# ---------------------------------------------------------------------------
# Source s6 container environment
#
# Docker env vars are NOT inherited by services in HA's s6-based containers.
# We source them from the s6 container environment directory. The Supervisor
# token is injected as HASSIO_TOKEN — we normalize to SUPERVISOR_TOKEN.
# ---------------------------------------------------------------------------
if [ -d /run/s6/container_environment ]; then
    for f in /run/s6/container_environment/*; do
        export "$(basename "$f")=$(cat "$f")"
    done
fi
export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-$HASSIO_TOKEN}"

# ---------------------------------------------------------------------------
# 1. Read add-on options
# ---------------------------------------------------------------------------
OPTIONS="/data/options.json"

PROVIDER=$(jq -r '.provider // "anthropic"' "$OPTIONS")
API_KEY=$(jq -r '.api_key // ""' "$OPTIONS")
MODEL=$(jq -r '.model // ""' "$OPTIONS")
SMALL_MODEL=$(jq -r '.small_model // ""' "$OPTIONS")
OLLAMA_HOST=$(jq -r '.ollama_host // ""' "$OPTIONS")
OLLAMA_KEEP_ALIVE=$(jq -r '.ollama_keep_alive // ""' "$OPTIONS")
GITHUB_TOKEN=$(jq -r '.github_token // ""' "$OPTIONS")

# ---------------------------------------------------------------------------
# 2. Set provider defaults & environment variables
#
# The api_key option is mapped to the correct environment variable based on
# the selected provider. OpenCode reads these env vars automatically.
#
# For providers not listed here, set environment variables manually in the
# container or extend this script.
# ---------------------------------------------------------------------------
case "$PROVIDER" in
  anthropic)
    DEFAULT_MODEL="anthropic/claude-sonnet-4-20250514"
    DEFAULT_SMALL="anthropic/claude-sonnet-4-20250514"
    [ -n "$API_KEY" ] && export ANTHROPIC_API_KEY="$API_KEY"
    PROVIDER_CONFIG='"anthropic": {}'
    ;;
  openai)
    DEFAULT_MODEL="openai/gpt-4o"
    DEFAULT_SMALL="openai/gpt-4o-mini"
    [ -n "$API_KEY" ] && export OPENAI_API_KEY="$API_KEY"
    PROVIDER_CONFIG='"openai": {}'
    ;;
  google)
    DEFAULT_MODEL="google/gemini-2.0-flash"
    DEFAULT_SMALL="google/gemini-2.0-flash"
    [ -n "$API_KEY" ] && export GOOGLE_API_KEY="$API_KEY"
    PROVIDER_CONFIG='"google": {}'
    ;;
  ollama)
    # -------------------------------------------------------------------------
    # Ollama — local LLM inference via OpenAI-compatible API
    #
    # ollama_host:       URL of the Ollama server (required)
    # ollama_keep_alive: How long Ollama keeps models loaded (e.g. "5m", "24h")
    # model:            Ollama model name (e.g. "qwen3:8b", "llama3.2")
    # small_model:      Fast model for simple tasks (defaults to same as model)
    #
    # Common ollama_host values:
    #   - http://homeassistant:11434   (Ollama on same machine, host_network)
    #   - http://192.168.x.x:11434    (Ollama on another machine)
    #   - http://<addon-slug>:11434    (Ollama as HA add-on, internal port)
    # -------------------------------------------------------------------------
    DEFAULT_MODEL="ollama/qwen3:8b"
    DEFAULT_SMALL="ollama/qwen3:8b"

    if [ -z "$OLLAMA_HOST" ]; then
        echo "ERROR: ollama_host is required when provider is 'ollama'"
        echo "Set it to the URL of your Ollama server (e.g. http://homeassistant:11434)"
        exit 1
    fi

    # Strip trailing slash from host URL
    OLLAMA_HOST="${OLLAMA_HOST%/}"
    OLLAMA_BASE_URL="${OLLAMA_HOST}/v1"

    # Build keep_alive parameter for model fetch options
    OLLAMA_FETCH_OPTS=""
    if [ -n "$OLLAMA_KEEP_ALIVE" ]; then
        OLLAMA_FETCH_OPTS=', "fetch": {"options": {"body": {"keep_alive": "'"${OLLAMA_KEEP_ALIVE}"'"}}}'
    fi

    # Resolve model names (strip 'ollama/' prefix if user included it)
    RESOLVED_MODEL="${MODEL:-$DEFAULT_MODEL}"
    RESOLVED_MODEL="${RESOLVED_MODEL#ollama/}"
    RESOLVED_SMALL="${SMALL_MODEL:-$DEFAULT_SMALL}"
    RESOLVED_SMALL="${RESOLVED_SMALL#ollama/}"

    # Build models map — include both model and small_model if different
    if [ "$RESOLVED_MODEL" = "$RESOLVED_SMALL" ]; then
        MODELS_MAP="\"${RESOLVED_MODEL}\": {\"name\": \"${RESOLVED_MODEL}\"${OLLAMA_FETCH_OPTS}}"
    else
        MODELS_MAP="\"${RESOLVED_MODEL}\": {\"name\": \"${RESOLVED_MODEL}\"${OLLAMA_FETCH_OPTS}}, \"${RESOLVED_SMALL}\": {\"name\": \"${RESOLVED_SMALL}\"${OLLAMA_FETCH_OPTS}}"
    fi

    PROVIDER_CONFIG="\"ollama\": {\"npm\": \"@ai-sdk/openai-compatible\", \"name\": \"Ollama\", \"options\": {\"baseURL\": \"${OLLAMA_BASE_URL}\"}, \"models\": {${MODELS_MAP}}}"

    # Override MODEL/SMALL_MODEL with ollama/ prefix for opencode.json
    MODEL="ollama/${RESOLVED_MODEL}"
    SMALL_MODEL="ollama/${RESOLVED_SMALL}"

    echo "Ollama host: ${OLLAMA_HOST}"
    [ -n "$OLLAMA_KEEP_ALIVE" ] && echo "Keep alive : ${OLLAMA_KEEP_ALIVE}"
    ;;
  *)
    echo "WARNING: Unknown provider '$PROVIDER' — falling back to anthropic"
    PROVIDER="anthropic"
    DEFAULT_MODEL="anthropic/claude-sonnet-4-20250514"
    DEFAULT_SMALL="anthropic/claude-sonnet-4-20250514"
    [ -n "$API_KEY" ] && export ANTHROPIC_API_KEY="$API_KEY"
    PROVIDER_CONFIG='"anthropic": {}'
    ;;
esac

MODEL="${MODEL:-$DEFAULT_MODEL}"
SMALL_MODEL="${SMALL_MODEL:-$DEFAULT_SMALL}"

echo "Provider : $PROVIDER"
echo "Model    : $MODEL"
echo "Small    : $SMALL_MODEL"

# ---------------------------------------------------------------------------
# 3. Write OpenCode configuration
#
# SUPERVISOR_TOKEN is injected automatically by the HA Supervisor into every
# add-on container as an environment variable. It is a short-lived JWT that
# rotates on each restart. We pass it to hass-mcp so the AI can authenticate
# with the HA API without any manual token setup.
# ---------------------------------------------------------------------------
mkdir -p /root/.config/opencode

# Build optional GitHub MCP block
GITHUB_MCP=""
if [ -n "$GITHUB_TOKEN" ]; then
    GITHUB_MCP=$(cat <<-GMCP
    ,
    "github": {
      "type": "local",
      "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
      "enabled": true,
      "environment": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
      }
    }
GMCP
    )
    echo "GitHub MCP enabled"
fi

cat > /root/.config/opencode/opencode.json << OCEOF
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "${MODEL}",
  "small_model": "${SMALL_MODEL}",
  "provider": {
    ${PROVIDER_CONFIG}
  },
  "mcp": {
    "homeassistant": {
      "type": "local",
      "command": ["uvx", "hass-mcp"],
      "enabled": true,
      "environment": {
        "HA_URL": "http://supervisor/core",
        "HA_TOKEN": "${SUPERVISOR_TOKEN}"
      }
    }${GITHUB_MCP}
  },
  "permission": { "*": "allow" },
  "autoupdate": false
}
OCEOF

echo "OpenCode config written"

# ---------------------------------------------------------------------------
# 4. Initialize git in /config (HA config directory)
# ---------------------------------------------------------------------------
cd /config

if [ ! -d .git ]; then
    git config --global user.email "opencode@homeassistant.local"
    git config --global user.name "OpenCode"
    git init
    cat > .gitignore << 'GIEOF'
# Secrets & credentials
secrets.yaml
.storage/
.cloud/
.aws/
SERVICE_ACCOUNT.json

# Large/binary files
*.db
*.db-shm
*.db-wal
*.log
tts/
backups/
GIEOF
    git add .gitignore
    git commit -m "init: opencode git tracking" 2>/dev/null || true
    echo "Git initialized in /config"
fi

# ---------------------------------------------------------------------------
# 5. Discover ingress entry and generate nginx config
#
# The ingress path is unique per installation. We query the Supervisor API
# to get it, then substitute it into the nginx config template.
# ---------------------------------------------------------------------------
echo "Discovering ingress path..."

INGRESS_ENTRY=""
for i in $(seq 1 30); do
    INGRESS_ENTRY=$(curl -s \
        -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
        http://supervisor/addons/self/info \
        | jq -r '.data.ingress_entry // empty' 2>/dev/null)
    if [ -n "$INGRESS_ENTRY" ]; then
        break
    fi
    echo "  Waiting for Supervisor API... (attempt $i)"
    sleep 2
done

if [ -z "$INGRESS_ENTRY" ]; then
    echo "ERROR: Could not discover ingress entry from Supervisor API"
    echo "Falling back to passthrough proxy (ingress rewriting disabled)"
    INGRESS_ENTRY=""
fi

echo "Ingress entry: ${INGRESS_ENTRY}"

sed "s|__INGRESS_ENTRY__|${INGRESS_ENTRY}|g" \
    /etc/nginx/nginx.conf.template \
    > /etc/nginx/http.d/opencode.conf

echo "nginx config generated"

# ---------------------------------------------------------------------------
# 6. Start nginx + OpenCode
#
# XDG_STATE_HOME is set to /data/state so that OpenCode persists sessions,
# conversation history, and other state across add-on restarts and HA reboots.
# /data/ is the only directory that survives container recreation in HA add-ons.
# ---------------------------------------------------------------------------
export XDG_STATE_HOME="/data/state"
mkdir -p "$XDG_STATE_HOME"

nginx
echo "nginx listening on port 8099 (ingress)"

echo "Starting OpenCode server on 127.0.0.1:19876..."
echo "Sessions persist at: ${XDG_STATE_HOME}/opencode/"
exec opencode serve --hostname 127.0.0.1 --port 19876
