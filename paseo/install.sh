#!/usr/bin/env bash
# Install or update the Paseo daemon and the agent CLIs on every start.
#
# Neither is part of the image, so the add-on version in config.yaml only has to change for changes
# to the add-on itself (extra tools, the ingress wrapper, this script) and not for every upstream
# Paseo release: `paseo_version` picks the daemon version, and `auto_update` decides whether the
# dist-tags are resolved again on each start. Upgrading is then an option change in the
# Configuration tab plus a restart.
#
# The daemon is installed with npm's global layout (lib/node_modules, bin) into a per-version
# directory under /data, so it survives add-on updates and older versions stay available to roll
# back to. /etc/paseo-server-entry points upstream's own entrypoint at the installed one, so
# starting the daemon is unchanged.
#
# The agent CLIs go into the persistent home's npm prefix, which run.sh has already put on PATH for
# the daemon, agents and terminals.
#
# Runs as root, from run.sh, before the daemon starts. It never runs anything as `paseo`.
set -euo pipefail
umask 022

APP_ROOT="${PASEO_APP_ROOT:-/data/paseo-app}"
WEB_ASSETS="${PASEO_WEB_ASSETS:-/opt/paseo-addon/web}"
# Upstream's image installs the daemon globally and points /etc/paseo-server-entry at it, so the
# daemon and CLI keep the npm layout (lib/node_modules, bin) here too. npm decides on its own where
# @getpaseo/server lands -- hoisted, or nested under @getpaseo/cli -- so its directory is looked up
# per install and remembered in .entry next to the version marker.
#
# PASEO_APP_ROOT, PASEO_WEB_ASSETS and PASEO_SERVER_ENTRY_FILE can move the three paths off their
# defaults, so a run outside the container (as a normal user, for tests) does not need /opt or
# /etc. run.sh does not set them.
SERVER_ENTRY_FILE="${PASEO_SERVER_ENTRY_FILE:-/etc/paseo-server-entry}"
DAEMON_PACKAGE="@getpaseo/cli"
SERVER_PACKAGE_DIR="@getpaseo/server"
SERVER_ENTRY_REL="dist/scripts/supervisor-entrypoint.js"
AGENT_CLIS=( "@anthropic-ai/claude-code" "@openai/codex" "opencode-ai" )

REQUESTED_VERSION="${1:-latest}"
case "${2:-true}" in
    true | 1 | yes | on) AUTO_UPDATE=1 ;;
    *) AUTO_UPDATE=0 ;;
esac

log() {
    echo "[paseo-addon] $*"
}

# npm's global layout puts the agent CLIs in the home directory so they persist; fall back to the
# same place run.sh uses if it did not export one.
export NPM_CONFIG_PREFIX="${NPM_CONFIG_PREFIX:-${HOME}/.npm-global}"
export PATH="${NPM_CONFIG_PREFIX}/bin:${PATH}"

# npm's download cache would otherwise land in ${HOME}/.npm: hundreds of MB that end up in every
# backup, owned by root inside the `paseo` home. Nothing here reuses it, so it lives in a temporary
# directory that goes away with this script.
NPM_CONFIG_CACHE="$(mktemp -d)"
export NPM_CONFIG_CACHE
trap 'rm -rf "${NPM_CONFIG_CACHE}"' EXIT

# A version ends up as a directory name and as part of an npm spec, so it is checked before it is
# used as either: only what semver and npm dist-tags can hold, never empty, never a flag, never a
# path. That also keeps an unusable version from turning `rm -rf "${target}"` into a mistake.
valid_version() {
    case "$1" in
        '' | . | .. | -* | */* | *[!0-9A-Za-z.+_-]*) return 1 ;;
    esac
}

# Echo the concrete version an npm spec resolves to, or fail if there is none. Used for dist-tags
# (`latest`, `beta`) and for the exact versions a user may pin; every version that reaches a path
# has been through here, so it is always a plain version number.
resolve_version() {
    local resolved
    resolved="$(npm view "$1" version 2>/dev/null | tr -d '\r' | tail -n 1)" || return 1
    [[ "${resolved}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]] || return 1
    printf '%s' "${resolved}"
}

# Version of the daemon the `current` symlink points at, if any.
installed_version() {
    [ -f "${APP_ROOT}/current/.installed" ] || return 1
    cat "${APP_ROOT}/current/.installed"
}

# The version to run. A dist-tag with auto_update off keeps whatever is already installed, so
# "latest" means "the latest when it was installed".
desired_version() {
    local requested="$1" installed
    if [[ ! "${requested}" =~ ^[0-9] ]] && [ "${AUTO_UPDATE}" != "1" ]; then
        if installed="$(installed_version)"; then
            printf '%s' "${installed}"
            return 0
        fi
    fi
    resolve_version "${DAEMON_PACKAGE}@${requested}"
}

# One "npm ls -g" for all three agent CLIs, as "<package> <version or empty>" lines.
installed_agent_clis() {
    npm ls -g --depth=0 --json 2>/dev/null | node -e '
        let raw = "";
        process.stdin.on("data", (chunk) => (raw += chunk)).on("end", () => {
            let tree = {};
            try { tree = JSON.parse(raw); } catch {}
            const deps = tree.dependencies || {};
            for (const name of process.argv.slice(1)) {
                const entry = deps[name];
                process.stdout.write(`${name} ${(entry && entry.version) || ""}\n`);
            }
        });
    ' "${AGENT_CLIS[@]}" || true
}

# The agent CLIs follow auto_update as well, but are always installed when missing.
ensure_agent_clis() {
    local package installed wanted changed=0
    while read -r package installed; do
        [ -n "${package}" ] || continue
        if [ -n "${installed}" ] && [ "${AUTO_UPDATE}" != "1" ]; then
            continue
        fi
        if ! wanted="$(resolve_version "${package}")"; then
            log "WARNING: could not resolve ${package} on npm; leaving ${installed:-nothing} installed."
            continue
        fi
        [ "${installed}" = "${wanted}" ] && continue
        log "Installing ${package}@${wanted} (installed: ${installed:-none})."
        if npm install -g --no-audit --no-fund --loglevel=warn "${package}@${wanted}" </dev/null; then
            changed=1
        else
            log "WARNING: could not install ${package}@${wanted}; keeping ${installed:-none}."
        fi
    done < <(installed_agent_clis)
    if [ "${changed}" = "1" ]; then
        # Installed as root, but the daemon and agents run as `paseo` and should be able to add
        # global packages of their own.
        chown -R paseo:paseo "${NPM_CONFIG_PREFIX}"
    fi
}

# The daemon entry point inside an installed prefix, as a path relative to that prefix. npm decides
# on its own where @getpaseo/server lands -- hoisted, or nested under @getpaseo/cli -- so the file is
# looked up per install and remembered in .entry, and nothing has to search the tree on later starts.
find_server_entry() {
    local prefix="$1" found
    found="$(find "${prefix}/lib/node_modules" -type f \
        -path "*/${SERVER_PACKAGE_DIR}/${SERVER_ENTRY_REL}" -print -quit 2>/dev/null)" || return 1
    [ -n "${found}" ] || return 1
    printf '%s' "${found#"${prefix}"/}"
}

server_entry() {
    local prefix="$1" relative
    relative="$(cat "${prefix}/.entry" 2>/dev/null || true)"
    if [ -z "${relative}" ]; then
        relative="$(find_server_entry "${prefix}")" || return 1
        printf '%s\n' "${relative}" > "${prefix}/.entry"
    fi
    printf '%s' "${prefix}/${relative}"
}

# Make the bundled web UI configure itself for this add-on's daemon: add the setup page next to the
# app and a small redirect to it (only when the browser has no saved hosts) at the top of <head>.
# This ran while the image was built; the web UI now arrives with every installed version, so it
# runs per version here, and again on each start so that a changed page reaches installed versions.
patch_web_ui() {
    local server_dir="$1" web_ui="${1}/dist/server/web-ui" snippet
    if [ ! -f "${web_ui}/index.html" ]; then
        log "WARNING: ${web_ui}/index.html is missing; the setup page and redirect were not added."
        return 0
    fi
    cp "${WEB_ASSETS}/paseo-setup.html" "${web_ui}/paseo-setup.html"
    # The daemon serves the pre-compressed copies when they are there, which would be the unpatched
    # originals.
    rm -f "${web_ui}/paseo-setup.html.gz" "${web_ui}/paseo-setup.html.br" \
          "${web_ui}/index.html.gz" "${web_ui}/index.html.br"
    snippet="$(tr -d '\n' < "${WEB_ASSETS}/index-redirect.html")"
    if grep -qF "${snippet}" "${web_ui}/index.html"; then
        return 0
    fi
    if ! SNIPPET="${snippet}" node -e '
        const fs = require("fs");
        const head = fs.readFileSync(process.argv[1], "utf8");
        if (!/<head>/i.test(head)) process.exit(1);
        fs.writeFileSync(process.argv[1], head.replace(/<head>/i, (m) => m + process.env.SNIPPET));
    ' "${web_ui}/index.html"; then
        log "WARNING: no <head> in ${web_ui}/index.html; skipping the setup redirect."
    fi
    grep -q 'paseo-setup.html' "${web_ui}/index.html" \
        || log "WARNING: the setup page link is missing from ${web_ui}/index.html."
}

# Install one version into its own directory and make it current. Building it under a temporary name
# and moving it into place keeps a half-installed version from ever being picked up.
install_daemon() {
    local version="$1"
    local target="${APP_ROOT}/${version}" tmp entry entry_rel
    if ! valid_version "${version}"; then
        log "ERROR: '${version}' is not a usable Paseo version."
        return 1
    fi
    if [ -f "${target}/.installed" ]; then
        log "Paseo ${version} is already installed."
    else
        tmp="${APP_ROOT}/.tmp-${version}.$$"
        rm -rf "${tmp}"
        mkdir -p "${tmp}"
        log "Installing Paseo ${version} (npm downloads about 500 MB; a few minutes)."
        if ! npm install -g --prefix "${tmp}" --no-audit --no-fund --loglevel=warn \
                "${DAEMON_PACKAGE}@${version}"; then
            log "ERROR: could not install ${DAEMON_PACKAGE}@${version}."
            rm -rf "${tmp}"
            return 1
        fi
        if ! entry_rel="$(find_server_entry "${tmp}")"; then
            log "ERROR: ${DAEMON_PACKAGE}@${version} came without ${SERVER_PACKAGE_DIR}/${SERVER_ENTRY_REL}."
            rm -rf "${tmp}"
            return 1
        fi
        printf '%s\n' "${entry_rel}" > "${tmp}/.entry"
        printf '%s\n' "${version}" > "${tmp}/.installed"
        rm -rf "${target:?}"
        mv "${tmp}" "${target}"
    fi
    if ! entry="$(server_entry "${target}")" || [ ! -f "${entry}" ] || ! node --check "${entry}"; then
        log "ERROR: Paseo ${version} has no usable daemon entry point."
        return 1
    fi
    if [ -d "${APP_ROOT}/current" ] && [ ! -L "${APP_ROOT}/current" ]; then
        rm -rf "${APP_ROOT:?}/current"
    fi
    ln -sfn "${target}" "${APP_ROOT}/current"
    patch_web_ui "${entry%"${SERVER_ENTRY_REL}"}"
    printf '%s\n' "${APP_ROOT}/current/${entry#"${target}"/}" > "${SERVER_ENTRY_FILE}"
    node --check "${APP_ROOT}/current/${entry#"${target}"/}"
}

# Keep the current version and the newest other one, so a rollback target survives an upgrade.
prune_versions() {
    local current other name
    current="$(installed_version || true)"
    [ -n "${current}" ] || return 0
    other=""
    while read -r name; do
        [ "${name}" = "${current}" ] || other="${name}"
    done < <(find "${APP_ROOT}" -mindepth 1 -maxdepth 1 -type d -name '[0-9]*' -printf '%f\n' | sort -V)
    while read -r name; do
        if [ "${name}" != "${current}" ] && [ "${name}" != "${other}" ]; then
            log "Removing the older Paseo ${name} install."
            rm -rf "${APP_ROOT:?}/${name}"
        fi
    done < <(find "${APP_ROOT}" -mindepth 1 -maxdepth 1 -type d -name '[0-9]*' -printf '%f\n')
    if [ -d "${APP_ROOT}" ]; then
        find "${APP_ROOT}" -mindepth 1 -maxdepth 1 -type d -name '.tmp-*' -exec rm -rf {} +
    fi
}

main() {
    mkdir -p "${APP_ROOT}"
    ensure_agent_clis

    local version installed
    if ! version="$(desired_version "${REQUESTED_VERSION}")"; then
        version=""
        if installed="$(installed_version)"; then
            log "WARNING: could not resolve '${REQUESTED_VERSION}' as a ${DAEMON_PACKAGE} version or dist-tag; staying on ${installed}."
            version="${installed}"
        else
            log "ERROR: '${REQUESTED_VERSION}' is not a ${DAEMON_PACKAGE} version or dist-tag, and there is nothing installed to fall back to."
            exit 1
        fi
    fi
    if ! install_daemon "${version}"; then
        if [ "${version}" != "$(installed_version || true)" ] && installed="$(installed_version)"; then
            log "WARNING: falling back to the installed Paseo ${installed}."
            install_daemon "${installed}" || exit 1
        else
            exit 1
        fi
    fi
    prune_versions
    log "Paseo $(installed_version) is ready (${APP_ROOT}/current)."
}

main
