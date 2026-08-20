#!/usr/bin/with-contenv bashio
# bashio enables bash strict mode (-e/-u/-o pipefail) on top of what this script sets; that turns
# any transient benign failure (e.g. a client disconnect) into a silent add-on crash, so it's
# explicitly turned back off (same pattern as this repo's rustdesk-server and fleet-telemetry
# run.sh scripts).
set +e +u +E +o pipefail

# Persistent $HOME (shell history, dotfiles) under Home Assistant's /data volume — survives
# add-on restarts/updates. The tmux session's own working directory is set separately below.
mkdir -p /data/home
export HOME=/data/home

# -w: permit-write (required for interactive input, not just read-only viewing).
# --no-auth: webtmux's own HTTP Basic Auth is redundant behind Home Assistant's ingress login,
# and its browser popup doesn't play well inside the ingress iframe. No host port is published
# in config.yaml, so ingress is the only way in.
# tmux new-session -A -s main -c /config: attach to (or create) a single persistent session
# named "main", starting in the Home Assistant config directory. Reconnecting/reopening the
# ingress tab re-attaches to the same session; the tmux server itself doesn't survive a container
# restart, which is inherent to tmux, not something this add-on can work around.
exec /usr/local/bin/webtmux -w -p 8080 -a 0.0.0.0 --no-auth tmux new-session -A -s main -c /config
