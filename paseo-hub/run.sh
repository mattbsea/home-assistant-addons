#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Paseo Hub add-on..."

if ! command -v paseo-hub > /dev/null 2>&1; then
    bashio::log.fatal "paseo-hub not found in PATH"
    exit 1
fi

# Embedded database (PGlite) and the generated auth secret persist under /data.
export PASEO_HUB_DATA_DIR="/data/paseo-hub"
mkdir -p "${PASEO_HUB_DATA_DIR}"
export PORT=3000
export PASEO_HUB_BIND="0.0.0.0"
export PASEO_HUB_APP_URL="$(bashio::config 'app_url')"

if bashio::config.has_value 'trusted_client_ip_header'; then
    export PASEO_HUB_TRUSTED_CLIENT_IP_HEADER="$(bashio::config 'trusted_client_ip_header')"
fi

# PostgreSQL replaces the embedded database when set (required for more than one Hub process).
if bashio::config.has_value 'database_url'; then
    export DATABASE_URL="$(bashio::config 'database_url')"
    bashio::log.info "Using PostgreSQL from database_url"
else
    bashio::log.info "Using embedded database in ${PASEO_HUB_DATA_DIR}"
fi

# Unattended first account. Hub validates the password on every start and crashes on a bad one.
BOOT_EMAIL=""; BOOT_PASSWORD=""
bashio::config.has_value 'bootstrap_owner_email' && BOOT_EMAIL="$(bashio::config 'bootstrap_owner_email')"
bashio::config.has_value 'bootstrap_owner_password' && BOOT_PASSWORD="$(bashio::config 'bootstrap_owner_password')"
if [ -n "${BOOT_EMAIL}" ] && [ -n "${BOOT_PASSWORD}" ]; then
    if [ "${#BOOT_PASSWORD}" -lt 12 ] || [ "${#BOOT_PASSWORD}" -gt 128 ]; then
        bashio::exit.nok "bootstrap_owner_password must be 12-128 characters"
    fi
    export PASEO_BOOTSTRAP_OWNER_EMAIL="${BOOT_EMAIL}"
    export PASEO_BOOTSTRAP_OWNER_PASSWORD="${BOOT_PASSWORD}"
    export PASEO_BOOTSTRAP_ORGANIZATION="Home"
    if bashio::config.has_value 'bootstrap_organization'; then
        PASEO_BOOTSTRAP_ORGANIZATION="$(bashio::config 'bootstrap_organization')"
    fi
elif [ -n "${BOOT_EMAIL}${BOOT_PASSWORD}" ]; then
    bashio::log.warning "Set both bootstrap_owner_email and bootstrap_owner_password; skipping first-account bootstrap"
fi

# Options that map one-to-one onto Hub environment variables; unset options are not exported.
export_option() {
    local option="$1" variable="$2"
    if bashio::config.has_value "${option}"; then
        export "${variable}=$(bashio::config "${option}")"
    fi
}
export_option auth_secret PASEO_HUB_AUTH_SECRET
export_option github_app_slug GITHUB_APP_SLUG
export_option github_app_id GITHUB_APP_ID
export_option github_app_client_id GITHUB_APP_CLIENT_ID
export_option github_app_client_secret GITHUB_APP_CLIENT_SECRET
export_option github_app_private_key GITHUB_APP_PRIVATE_KEY
export_option github_app_private_key_path GITHUB_APP_PRIVATE_KEY_PATH
export_option github_webhook_secret GITHUB_WEBHOOK_SECRET
export_option slack_transport SLACK_TRANSPORT
export_option slack_app_id SLACK_APP_ID
export_option slack_app_token SLACK_APP_TOKEN
export_option slack_client_id SLACK_CLIENT_ID
export_option slack_client_secret SLACK_CLIENT_SECRET
export_option slack_signing_secret SLACK_SIGNING_SECRET
export_option discord_client_id DISCORD_CLIENT_ID
export_option discord_client_secret DISCORD_CLIENT_SECRET
export_option discord_bot_token DISCORD_BOT_TOKEN

# Any other Hub variables. Names the add-on manages itself are skipped.
for name in $(bashio::config 'env_vars|keys'); do
    key="$(bashio::config "env_vars[${name}].name")"
    value="$(bashio::config "env_vars[${name}].value")"
    [ "${value}" = "null" ] && value=""
    case "${key}" in
        PORT|PASEO_HUB_BIND|PASEO_HUB_APP_URL|PASEO_HUB_DATA_DIR|DATABASE_URL)
            bashio::log.warning "Ignoring env_vars entry ${key}; set it through the add-on options"
            continue ;;
    esac
    export "${key}=${value}"
done

bashio::log.info "Dashboard: ${PASEO_HUB_APP_URL}"
bashio::log.info "Connect a daemon with: paseo hub connect ${PASEO_HUB_APP_URL}"

exec paseo-hub
