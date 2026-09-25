#!/usr/bin/env bash
# No bashio on the upstream Paseo (Debian) base image -- read options.json with jq and call the
# Supervisor API with curl. Runs as root to prepare /data and the workspace, then hands off to
# upstream's paseo-docker-entrypoint, which drops to the non-root `paseo` user via gosu.
set -eu

OPTIONS_FILE="/data/options.json"
FALLBACK_PASSWORD_FILE="/data/.generated-password"

opt() {
    jq -r "$1" "${OPTIONS_FILE}"
}

log() {
    echo "[paseo-addon] $*"
}

# --- Password --------------------------------------------------------------------------------
# An empty option means "generate one": create it once, then persist it back into the add-on's
# own options so the user can read it under Settings -> Add-ons -> Paseo -> Configuration.
PASSWORD="$(opt '.password // ""')"
if [ -z "${PASSWORD}" ]; then
    if [ -s "${FALLBACK_PASSWORD_FILE}" ]; then
        PASSWORD="$(cat "${FALLBACK_PASSWORD_FILE}")"
    else
        PASSWORD="$(node -e "process.stdout.write(require('crypto').randomBytes(18).toString('base64url'))")"
    fi
    # /addons/self/options replaces the whole options object, so send every key back. The body
    # goes over stdin so the password never appears in a process list.
    if jq --arg p "${PASSWORD}" '{options: (. + {password: $p})}' "${OPTIONS_FILE}" \
        | curl -fsS -o /dev/null -X POST \
            -H "Authorization: Bearer ${SUPERVISOR_TOKEN:-}" \
            -H "Content-Type: application/json" \
            --data-binary @- \
            http://supervisor/addons/self/options; then
        rm -f "${FALLBACK_PASSWORD_FILE}"
        log "Generated a password and saved it to the add-on configuration (password option)."
    else
        # Keep the same password across restarts until the write-back succeeds.
        (umask 077 && printf '%s' "${PASSWORD}" > "${FALLBACK_PASSWORD_FILE}")
        log "ERROR: could not save the generated password to the add-on options; kept it in ${FALLBACK_PASSWORD_FILE}."
    fi
fi
export PASEO_PASSWORD="${PASSWORD}"
# Agents and Paseo terminals inherit this environment; don't hand them the Supervisor API.
unset SUPERVISOR_TOKEN

# --- Persistent home -------------------------------------------------------------------------
# Everything the daemon and the agent CLIs keep (Paseo state, Claude/Codex/OpenCode logins,
# config, caches) lives under /data so it survives restarts and updates.
export HOME=/data/home
# gosu doesn't set SHELL, so the daemon (and Paseo terminals) would fall back to dash.
export SHELL=/bin/bash
export PASEO_HOME="${HOME}/.paseo"
export CLAUDE_CONFIG_DIR="${HOME}/.claude"
export CODEX_HOME="${HOME}/.codex"
export XDG_CONFIG_HOME="${HOME}/.config"
export XDG_DATA_HOME="${HOME}/.local/share"
export XDG_STATE_HOME="${HOME}/.local/state"
export XDG_CACHE_HOME="${HOME}/.cache"
# Tools installed from a Paseo terminal persist when they land in the home directory: curl
# installers / uv tool / pipx -> ~/.local/bin, cargo install -> ~/.cargo/bin, and npm i -g ->
# ~/.npm-global (the image's global npm dir is root-owned and rebuilt on every update), and
# bun add -g -> ~/.bun/bin. Same list as /etc/profile.d/paseo-addon.sh, which covers login shells.
export NPM_CONFIG_PREFIX="${HOME}/.npm-global"
export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${HOME}/.bun/bin:${NPM_CONFIG_PREFIX}/bin:${PATH}"
for dir in "${HOME}" "${PASEO_HOME}" "${CLAUDE_CONFIG_DIR}" "${CODEX_HOME}" \
    "${XDG_CONFIG_HOME}" "${XDG_DATA_HOME}" "${XDG_STATE_HOME}" "${XDG_CACHE_HOME}" \
    "${HOME}/.local/bin" "${HOME}/.cargo/bin" "${HOME}/.bun/bin" "${NPM_CONFIG_PREFIX}/bin"; do
    mkdir -p "${dir}"
done
chown -R paseo:paseo "${HOME}"

WORKSPACE_DIR="$(opt '.workspace_dir // "/share/paseo"')"
mkdir -p "${WORKSPACE_DIR}"
if [ "$(stat -c '%u' "${WORKSPACE_DIR}")" = "0" ]; then
    chown paseo:paseo "${WORKSPACE_DIR}"
fi

# --- Daemon network settings -----------------------------------------------------------------
export PASEO_LISTEN="0.0.0.0:6767"
export PASEO_WEB_UI_ENABLED=true
export PASEO_RELAY_ENABLED=false
PASEO_HOSTNAMES="$(opt '(.hostnames // []) | join(",")')"
PASEO_TRUSTED_PROXIES="$(opt '(.trusted_proxies // ["loopback"]) | join(",")')"
export PASEO_HOSTNAMES PASEO_TRUSTED_PROXIES

# --- Agent credentials (optional; interactive logins from a Paseo terminal also work) ---------
CLAUDE_CODE_OAUTH_TOKEN="$(opt '.claude_code_oauth_token // ""')"
ANTHROPIC_API_KEY="$(opt '.anthropic_api_key // ""')"
OPENAI_API_KEY="$(opt '.openai_api_key // ""')"
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN}" ]; then export CLAUDE_CODE_OAUTH_TOKEN; else unset CLAUDE_CODE_OAUTH_TOKEN; fi
if [ -n "${ANTHROPIC_API_KEY}" ]; then export ANTHROPIC_API_KEY; else unset ANTHROPIC_API_KEY; fi
if [ -n "${OPENAI_API_KEY}" ]; then
    export OPENAI_API_KEY
    # Codex ignores OPENAI_API_KEY unless it has been logged in with it. Re-login whenever the
    # option changes (tracked by hash so the key itself isn't stored twice).
    KEY_HASH_FILE="/data/.openai-key.sha256"
    KEY_HASH="$(printenv OPENAI_API_KEY | sha256sum | cut -d' ' -f1)"
    if [ ! -s "${CODEX_HOME}/auth.json" ] || [ "$(cat "${KEY_HASH_FILE}" 2>/dev/null)" != "${KEY_HASH}" ]; then
        if printenv OPENAI_API_KEY | gosu paseo codex login --with-api-key >/dev/null 2>&1; then
            printf '%s' "${KEY_HASH}" > "${KEY_HASH_FILE}"
        else
            log "WARNING: codex login --with-api-key failed; log in from a Paseo terminal instead."
        fi
    fi
else
    unset OPENAI_API_KEY
fi

# --- User environment variables (env_vars option) ---------------------------------------------
# Exported to the daemon, so every agent and Paseo terminal inherits them. Applied after the
# add-on's own settings, so they can override those too, except for the few the add-on depends on.
RESERVED_ENV="HOME SHELL PATH PASEO_HOME PASEO_LISTEN PASEO_PASSWORD SUPERVISOR_TOKEN"
ENV_COUNT="$(opt '(.env_vars // []) | length')"
for i in $(seq 0 $((ENV_COUNT - 1))); do
    name="$(jq -r --argjson i "${i}" '.env_vars[$i].name // ""' "${OPTIONS_FILE}")"
    if ! [[ "${name}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
        log "WARNING: env_vars entry $((i + 1)) has an invalid name '${name}'; skipped."
        continue
    fi
    case " ${RESERVED_ENV} " in
        *" ${name} "*)
            log "WARNING: env_vars entry ${name} is managed by the add-on; skipped."
            continue
            ;;
    esac
    value="$(jq -r --argjson i "${i}" '.env_vars[$i].value // ""' "${OPTIONS_FILE}")"
    export "${name}=${value}"
    log "Set environment variable ${name} from env_vars."
done
unset RESERVED_ENV ENV_COUNT i name value

# --- Sidebar (ingress) wrapper ---------------------------------------------------------------
PASEO_EXTERNAL_URL="$(opt '.external_url // ""')"
export PASEO_EXTERNAL_URL
(
    while :; do
        gosu paseo node /opt/paseo-ingress/server.js || log "sidebar panel exited ($?); restarting"
        sleep 2
    done
) &

# --- Default workspace -----------------------------------------------------------------------
# The web UI's "Add project -> New directory" parent picker only lists existing workspaces'
# project roots (its directory search is confined to $HOME, where there's nothing to find), so
# whenever the daemon has no workspaces (fresh install, or all projects removed) it is empty and
# nothing can be created. In that case register workspace_dir as a "Home Assistant" workspace
# once the daemon is up. Left alone as soon as any workspace exists.
rm -f /data/.workspace-seeded  # 1.1.2's create-once marker; no longer used
(
    for _ in $(seq 1 90); do
        curl -fs -o /dev/null http://127.0.0.1:6767/api/health && break
        sleep 2
    done
    paseo_cli() { gosu paseo paseo --host 127.0.0.1:6767 "$@"; }
    if ! WORKSPACES="$(paseo_cli workspace ls --json 2>/dev/null)"; then
        log "WARNING: could not list workspaces; skipping default workspace check."
    elif [ "$(printf '%s' "${WORKSPACES}" | jq 'length')" = "0" ]; then
        if paseo_cli workspace create --isolation local --path "${WORKSPACE_DIR}" \
            --title "Home Assistant" --json >/dev/null 2>&1; then
            log "No workspaces yet: registered ${WORKSPACE_DIR} as the \"Home Assistant\" workspace."
        else
            log "WARNING: could not register ${WORKSPACE_DIR} as a workspace; will retry next start."
        fi
    fi
) &

log "Starting Paseo daemon on ${PASEO_LISTEN} (hostnames: ${PASEO_HOSTNAMES:-default}, workspace: ${WORKSPACE_DIR})"
cd "${WORKSPACE_DIR}"
exec /usr/local/bin/paseo-docker-entrypoint
