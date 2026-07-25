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
    DEFAULT_MODEL="ollama/qwen3:8b"
    DEFAULT_SMALL="ollama/qwen3:8b"

    if [ -z "$OLLAMA_HOST" ]; then
        echo "ERROR: ollama_host is required when provider is 'ollama'"
        echo "Set it to the URL of your Ollama server (e.g. http://homeassistant:11434)"
        exit 1
    fi

    OLLAMA_HOST="${OLLAMA_HOST%/}"
    OLLAMA_BASE_URL="${OLLAMA_HOST}/v1"

    OLLAMA_FETCH_OPTS=""
    if [ -n "$OLLAMA_KEEP_ALIVE" ]; then
        OLLAMA_FETCH_OPTS=', "fetch": {"options": {"body": {"keep_alive": "'"${OLLAMA_KEEP_ALIVE}"'"}}}'
    fi

    RESOLVED_MODEL="${MODEL:-$DEFAULT_MODEL}"
    RESOLVED_MODEL="${RESOLVED_MODEL#ollama/}"
    RESOLVED_SMALL="${SMALL_MODEL:-$DEFAULT_SMALL}"
    RESOLVED_SMALL="${RESOLVED_SMALL#ollama/}"

    if [ "$RESOLVED_MODEL" = "$RESOLVED_SMALL" ]; then
        MODELS_MAP="\"${RESOLVED_MODEL}\": {\"name\": \"${RESOLVED_MODEL}\"${OLLAMA_FETCH_OPTS}}"
    else
        MODELS_MAP="\"${RESOLVED_MODEL}\": {\"name\": \"${RESOLVED_MODEL}\"${OLLAMA_FETCH_OPTS}}, \"${RESOLVED_SMALL}\": {\"name\": \"${RESOLVED_SMALL}\"${OLLAMA_FETCH_OPTS}}"
    fi

    PROVIDER_CONFIG="\"ollama\": {\"npm\": \"@ai-sdk/openai-compatible\", \"name\": \"Ollama\", \"options\": {\"baseURL\": \"${OLLAMA_BASE_URL}\"}, \"models\": {${MODELS_MAP}}}"

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
# 3. Generate or load server password
#
# A random password is generated on first start and saved to /data so it
# persists across container recreations. This password protects the
# opencode serve web UI.
# ---------------------------------------------------------------------------
PASSWORD_FILE="/data/opencode-password"

if [ -f "$PASSWORD_FILE" ]; then
    PASSWORD=$(cat "$PASSWORD_FILE")
    echo "Loaded existing password from ${PASSWORD_FILE}"
else
    PASSWORD=$(openssl rand -hex 16)
    echo "$PASSWORD" > "$PASSWORD_FILE"
    echo "Generated new password and saved to ${PASSWORD_FILE}"
fi

echo ""
echo "============================================"
echo "  OpenCode password: ${PASSWORD}"
echo "============================================"
echo ""

# ---------------------------------------------------------------------------
# 4. Write OpenCode configuration
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
# 5. Initialize git in /config (HA config directory)
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
# 6. Start OpenCode server
#
# Binds to 0.0.0.0 so it's accessible from outside the container on the
# configured host port. XDG_STATE_HOME is set to /data/state so that
# OpenCode persists sessions across container recreations.
# ---------------------------------------------------------------------------
export XDG_STATE_HOME="/data/state"
export OPENCODE_SERVER_PASSWORD="$PASSWORD"
mkdir -p "$XDG_STATE_HOME"

echo "Starting OpenCode server on 0.0.0.0:4096..."
echo "Sessions persist at: ${XDG_STATE_HOME}/opencode/"
exec opencode serve --hostname 0.0.0.0 --port 4096
