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

# Provider apps (GITHUB_APP_*, SLACK_*, DISCORD_*) and other Hub variables. Names the add-on
# manages itself are skipped.
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
