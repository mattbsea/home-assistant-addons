#!/usr/bin/with-contenv bashio
# bashio enables bash strict mode (-e/-u/-o pipefail) on top of what this script sets; that turns
# any transient benign failure (e.g. a client disconnect) into a silent add-on crash, so it's
# explicitly turned back off (same pattern as this repo's rustdesk-server and fleet-telemetry
# run.sh scripts).
set +e +u +E +o pipefail

USERNAME="$(bashio::config 'username')"
if [ -z "${USERNAME}" ]; then
    USERNAME="webtmux"
fi

# Persistent home under Home Assistant's /data volume (survives add-on restarts/updates),
# symlinked to the conventional /home/<username> path so the terminal's default working
# directory and $HOME both land there. Changing the username option later just creates a new
# home; the old one is left behind under /data/home, harmless.
mkdir -p "/data/home/${USERNAME}"
mkdir -p /home
ln -sfn "/data/home/${USERNAME}" "/home/${USERNAME}"

# Idempotent — safe to re-run every boot. -M: skip home-dir creation, the symlink above already
# provides it.
if ! id -u "${USERNAME}" >/dev/null 2>&1; then
    useradd -M -d "/home/${USERNAME}" -s /bin/bash "${USERNAME}"
fi
chown -R "${USERNAME}:${USERNAME}" "/data/home/${USERNAME}"

# Passwordless sudo: this is the non-root user's escape hatch onto the root-owned HA volumes
# (/config, /share, /addons, /backup, /media are all root:root 755, read-only to non-root).
# No password because access is already gated by Home Assistant's ingress login — same trust
# model as webtmux's own --no-auth below, an in-terminal password would just be friction.
echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/${USERNAME}"
chmod 0440 "/etc/sudoers.d/${USERNAME}"

export HOME="/home/${USERNAME}"
cd "${HOME}" || exit 1

run_as_user() {
    setpriv --reuid="${USERNAME}" --regid="${USERNAME}" --init-groups -- "$@"
}

# The tmux session is pre-built here, as the target user, rather than left to webtmux's own
# internal tmux controller (pkg/tmux/controller.go). That controller does its own
# `tmux has-session` / `tmux new-session -d -s main` the moment the server starts — before any
# client connects, with no working-directory or extra-window option of its own — so it's the
# wrong place to hang per-window setup off of; building the whole session upfront and letting
# webtmux's own new-session below just attach to it (see the -A flag) sidesteps that entirely.
if ! run_as_user tmux has-session -t main 2>/dev/null; then
    run_as_user tmux new-session -d -s main -c "${HOME}"
    # A window whose command finishes or crashes stays visible (showing its output) instead of
    # vanishing — matters for one-shot commands, not just long-running ones like dev servers.
    run_as_user tmux set-option -t main remain-on-exit on

    if bashio::config.has_value 'windows'; then
        count=$(bashio::config 'windows | length')
        for ((i = 0; i < count; i++)); do
            win_name=$(bashio::config "windows[${i}].name" '')
            win_dir=$(bashio::config "windows[${i}].directory" '')
            win_cmd=$(bashio::config "windows[${i}].command" '')

            # Handle bashio "null" for unset optional values
            if [ "${win_dir}" = "null" ] || [ -z "${win_dir}" ]; then
                win_dir="${HOME}"
            fi

            # Only chown a directory we just created — never re-own something that already
            # existed (e.g. pointing a window at /config shouldn't quietly change who owns it).
            if [ ! -d "${win_dir}" ]; then
                mkdir -p "${win_dir}" && chown "${USERNAME}:${USERNAME}" "${win_dir}"
            fi

            if [ "${win_name}" = "null" ] || [ -z "${win_name}" ]; then
                win_name="$(basename "${win_dir}")"
            fi

            bashio::log.info "Window '${win_name}': ${win_cmd} (in ${win_dir})"
            run_as_user tmux new-window -t main -n "${win_name}" -c "${win_dir}" "${win_cmd}"
        done
    fi
fi

# -w: permit-write (required for interactive input, not just read-only viewing).
# --no-auth: webtmux's own HTTP Basic Auth is redundant behind Home Assistant's ingress login,
# and its browser popup doesn't play well inside the ingress iframe. No host port is published
# in config.yaml, so ingress is the only way in.
# setpriv --reuid/--regid/--init-groups: drop from root to the configured user before exec'ing
# webtmux, so both it and the tmux/shell it spawns run unprivileged.
# tmux new-session -A -s main -c /home/<username>: attach to the session built above.
# Reconnecting/reopening the ingress tab re-attaches to the same session; the tmux server itself
# doesn't survive a container restart, which is inherent to tmux, not something this add-on can
# work around.
exec setpriv --reuid="${USERNAME}" --regid="${USERNAME}" --init-groups -- \
    /usr/local/bin/webtmux -w -p 8080 -a 0.0.0.0 --no-auth tmux new-session -A -s main -c "/home/${USERNAME}"
