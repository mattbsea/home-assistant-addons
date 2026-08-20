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

# -w: permit-write (required for interactive input, not just read-only viewing).
# --no-auth: webtmux's own HTTP Basic Auth is redundant behind Home Assistant's ingress login,
# and its browser popup doesn't play well inside the ingress iframe. No host port is published
# in config.yaml, so ingress is the only way in.
# setpriv --reuid/--regid/--init-groups: drop from root to the configured user before exec'ing
# webtmux, so both it and the tmux/shell it spawns run unprivileged.
# tmux new-session -A -s main -c /home/<username>: attach to (or create) a single persistent
# session named "main", starting in the user's home directory. Reconnecting/reopening the
# ingress tab re-attaches to the same session; the tmux server itself doesn't survive a container
# restart, which is inherent to tmux, not something this add-on can work around.
#
# The `cd` below matters beyond just this exec'd command: webtmux's own internal tmux controller
# (pkg/tmux/controller.go) runs `tmux has-session`/`tmux new-session -d -s main` itself at server
# startup — before any client connects and before the `-c` below ever runs — with no -c of its
# own, so it inherits whatever directory the webtmux *process* was started from. That first
# new-session call is what actually creates "main" and fixes its default-path; by the time our
# `-c` runs, -A just attaches to the already-existing session and -c is silently ignored. Without
# this cd, that session ends up defaulting to wherever the container's initial cwd was (`/`),
# regardless of -c here.
export HOME="/home/${USERNAME}"
cd "${HOME}" || exit 1
exec setpriv --reuid="${USERNAME}" --regid="${USERNAME}" --init-groups -- \
    /usr/local/bin/webtmux -w -p 8080 -a 0.0.0.0 --no-auth tmux new-session -A -s main -c "/home/${USERNAME}"
