#!/usr/bin/env bash

echo "============================================"
echo "  OpenCode HA Add-on Starting"
echo "============================================"

# ---------------------------------------------------------------------------
# Source s6 container environment
#
# Docker env vars are NOT inherited by services in HA's s6-based containers.
# We source them from the s6 container environment directory. The Supervisor
# token is injected as HASSIO_TOKEN — we normalize to SUPERVISOR_TOKEN.
# ---------------------------------------------------------------------------
echo "[init] Sourcing s6 container environment..."
S6_ENV_COUNT=0
if [ -d /run/s6/container_environment ]; then
    for f in /run/s6/container_environment/*; do
        export "$(basename "$f")=$(cat "$f")"
        S6_ENV_COUNT=$((S6_ENV_COUNT + 1))
    done
fi
echo "[init] Sourced ${S6_ENV_COUNT} environment variable(s) from s6"
export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-$HASSIO_TOKEN}"

if [ -n "$SUPERVISOR_TOKEN" ]; then
    echo "[init] Supervisor token: present (${#SUPERVISOR_TOKEN} chars)"
else
    echo "[init] WARNING: No Supervisor token found — HA API calls will fail"
fi

# ---------------------------------------------------------------------------
# 1. Read add-on options
# ---------------------------------------------------------------------------
echo "[options] Reading ${OPTIONS:-/data/options.json}..."
OPTIONS="/data/options.json"

if [ ! -f "$OPTIONS" ]; then
    echo "[options] ERROR: ${OPTIONS} not found — using defaults"
else
    echo "[options] Options file: present ($(wc -c < "$OPTIONS") bytes)"
fi

PROVIDER=$(jq -r '.provider // "anthropic"' "$OPTIONS")
API_KEY=$(jq -r '.api_key // ""' "$OPTIONS")
MODEL=$(jq -r '.model // ""' "$OPTIONS")
SMALL_MODEL=$(jq -r '.small_model // ""' "$OPTIONS")
OLLAMA_HOST=$(jq -r '.ollama_host // ""' "$OPTIONS")
OLLAMA_KEEP_ALIVE=$(jq -r '.ollama_keep_alive // ""' "$OPTIONS")
GITHUB_TOKEN=$(jq -r '.github_token // ""' "$OPTIONS")

echo "[options] Provider      : ${PROVIDER}"
echo "[options] Model (raw)   : ${MODEL:-<not set>}"
echo "[options] Small (raw)   : ${SMALL_MODEL:-<not set>}"
echo "[options] API key       : $([ -n "$API_KEY" ] && echo "set (${#API_KEY} chars)" || echo "not set")"
echo "[options] Ollama host   : ${OLLAMA_HOST:-<not set>}"
echo "[options] GitHub token  : $([ -n "$GITHUB_TOKEN" ] && echo "set (${#GITHUB_TOKEN} chars)" || echo "not set")"

# ---------------------------------------------------------------------------
# 2. Set provider defaults & environment variables
# ---------------------------------------------------------------------------
echo "[provider] Configuring provider '${PROVIDER}'..."
case "$PROVIDER" in
  anthropic)
    DEFAULT_MODEL="anthropic/claude-sonnet-4-20250514"
    DEFAULT_SMALL="anthropic/claude-sonnet-4-20250514"
    if [ -n "$API_KEY" ]; then
        export ANTHROPIC_API_KEY="$API_KEY"
        echo "[provider] ANTHROPIC_API_KEY exported"
    else
        echo "[provider] WARNING: No API key set — Anthropic calls will fail"
    fi
    PROVIDER_CONFIG='"anthropic": {}'
    ;;
  openai)
    DEFAULT_MODEL="openai/gpt-4o"
    DEFAULT_SMALL="openai/gpt-4o-mini"
    if [ -n "$API_KEY" ]; then
        export OPENAI_API_KEY="$API_KEY"
        echo "[provider] OPENAI_API_KEY exported"
    else
        echo "[provider] WARNING: No API key set — OpenAI calls will fail"
    fi
    PROVIDER_CONFIG='"openai": {}'
    ;;
  google)
    DEFAULT_MODEL="google/gemini-2.0-flash"
    DEFAULT_SMALL="google/gemini-2.0-flash"
    if [ -n "$API_KEY" ]; then
        export GOOGLE_API_KEY="$API_KEY"
        echo "[provider] GOOGLE_API_KEY exported"
    else
        echo "[provider] WARNING: No API key set — Google calls will fail"
    fi
    PROVIDER_CONFIG='"google": {}'
    ;;
  ollama)
    DEFAULT_MODEL="ollama/qwen3:8b"
    DEFAULT_SMALL="ollama/qwen3:8b"

    if [ -z "$OLLAMA_HOST" ]; then
        echo "[provider] ERROR: ollama_host is required when provider is 'ollama'"
        echo "[provider] Set it to the URL of your Ollama server (e.g. http://homeassistant:11434)"
        exit 1
    fi

    OLLAMA_HOST="${OLLAMA_HOST%/}"
    OLLAMA_BASE_URL="${OLLAMA_HOST}/v1"
    echo "[provider] Ollama base URL: ${OLLAMA_BASE_URL}"

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
    ;;
  *)
    echo "[provider] WARNING: Unknown provider '${PROVIDER}' — falling back to anthropic"
    PROVIDER="anthropic"
    DEFAULT_MODEL="anthropic/claude-sonnet-4-20250514"
    DEFAULT_SMALL="anthropic/claude-sonnet-4-20250514"
    if [ -n "$API_KEY" ]; then
        export ANTHROPIC_API_KEY="$API_KEY"
        echo "[provider] ANTHROPIC_API_KEY exported (fallback)"
    fi
    PROVIDER_CONFIG='"anthropic": {}'
    ;;
esac

MODEL="${MODEL:-$DEFAULT_MODEL}"
SMALL_MODEL="${SMALL_MODEL:-$DEFAULT_SMALL}"

echo "[provider] Final model     : ${MODEL}"
echo "[provider] Final small     : ${SMALL_MODEL}"

# ---------------------------------------------------------------------------
# 3. Generate or load server password
#
# A random password is generated on first start and saved to /data so it
# persists across container recreations. This password protects the
# opencode serve web UI.
# ---------------------------------------------------------------------------
echo "[password] Checking for existing password file..."
PASSWORD_FILE="/data/opencode-password"

if [ -f "$PASSWORD_FILE" ]; then
    PASSWORD=$(cat "$PASSWORD_FILE")
    echo "[password] Loaded existing password from ${PASSWORD_FILE} (${#PASSWORD} chars)"
else
    echo "[password] No password file found — generating random password..."
    PASSWORD=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c 32)
    echo "$PASSWORD" > "$PASSWORD_FILE"
    echo "[password] Generated new password and saved to ${PASSWORD_FILE}"
fi

echo ""
echo "============================================"
echo "  OpenCode password: ${PASSWORD}"
echo "============================================"
echo ""

# ---------------------------------------------------------------------------
# 4. Write OpenCode configuration
# ---------------------------------------------------------------------------
echo "[config] Writing OpenCode config to /root/.config/opencode/opencode.json..."
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
    echo "[config] GitHub MCP: enabled"
else
    echo "[config] GitHub MCP: disabled (no token)"
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

echo "[config] OpenCode config written ($(wc -c < /root/.config/opencode/opencode.json) bytes)"

# ---------------------------------------------------------------------------
# 5. Initialize git in /config (HA config directory)
# ---------------------------------------------------------------------------
echo "[git] Initializing git in /config..."
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
    echo "[git] Initialized git repo in /config"
else
    echo "[git] Git repo already exists in /config"
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

echo "[server] XDG_STATE_HOME  : ${XDG_STATE_HOME}"
echo "[server] Sessions dir   : ${XDG_STATE_HOME}/opencode/"
echo "[server] Password file   : ${PASSWORD_FILE}"
echo "[server] Listening on    : 0.0.0.0:4096"
echo "[server] Starting opencode serve..."
exec opencode serve --hostname 0.0.0.0 --port 4096
