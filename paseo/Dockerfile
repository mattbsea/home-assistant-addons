# BUILD_FROM is the official Paseo image: Debian bookworm + Node 22, gosu, and a non-root `paseo`
# user (uid 1000). Only that runtime is taken from it -- the daemon and the agent CLIs are installed
# on every start (see install.sh), so a new Paseo release is an option change rather than a new
# add-on version. Bump this default to move the runtime forward; nothing else in the image depends
# on the base image's own copy of Paseo. The image has no bashio, so run.sh reads
# /data/options.json with jq and talks to Supervisor with curl directly.
ARG BUILD_FROM=ghcr.io/getpaseo/paseo:0.10.1
FROM ${BUILD_FROM}

LABEL org.opencontainers.image.title="Home Assistant Add-on: Paseo" \
      org.opencontainers.image.description="Paseo daemon + web UI with Claude Code, Codex and OpenCode" \
      org.opencontainers.image.source="https://github.com/getpaseo/paseo" \
      org.opencontainers.image.licenses="Apache-2.0"

USER root

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends jq ripgrep unzip \
      python3 python3-venv python3-pip python-is-python3 openssh-client \
      wget nano vim tree build-essential \
      ffmpeg python3-opencv; \
    rm -rf /var/lib/apt/lists/*; \
    ffmpeg -version | head -n1; \
    python3 -c 'import cv2; print("OpenCV", cv2.__version__)'

# GitHub CLI from GitHub's official apt repository. Its login (`gh auth login`) is stored under
# $XDG_CONFIG_HOME/gh = /data/home/.config/gh, so it persists.
RUN set -eux; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg; \
    chmod a+r /usr/share/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends gh; \
    rm -rf /var/lib/apt/lists/*; \
    gh --version

# uv/uvx for Python tooling (`uv tool install` -> ~/.local/bin; uv-managed Pythons go to
# ~/.local/share/uv, so both persist).
RUN set -eux; \
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh; \
    uv --version

# yq (mikefarah's Go version; Debian's `yq` package is an unrelated jq wrapper).
RUN set -eux; \
    curl -fsSL -o /usr/local/bin/yq \
      "https://github.com/mikefarah/yq/releases/latest/download/yq_linux_$(dpkg --print-architecture)"; \
    chmod a+x /usr/local/bin/yq; \
    yq --version

# Bun + bunx (the installer needs unzip). `bun add -g` installs to ~/.bun (bin: ~/.bun/bin), so
# globally added packages persist.
RUN set -eux; \
    curl -fsSL https://bun.sh/install | env BUN_INSTALL=/usr/local bash; \
    ln -sf bun /usr/local/bin/bunx; \
    bun --version; \
    command -v bunx

# gosu sets HOME from /etc/passwd when it drops to `paseo`, which would override run.sh's
# HOME=/data/home with the image's ephemeral /home/paseo (so `~`, ~/.gitconfig, ~/.ssh and
# installers writing to ~/.local/bin would be lost on update). Point the account at /data/home.
RUN usermod --home /data/home paseo && test "$(getent passwd paseo | cut -d: -f6)" = /data/home

# User-installed tools live in the persistent home (see run.sh); put their bin dirs on PATH for
# login shells too (Paseo's provider checks use `sh -lc`, which resets PATH from /etc/profile).
COPY profile.d/paseo-addon.sh /etc/profile.d/paseo-addon.sh

# Claude Code, Codex and OpenCode (the set Paseo drives, same as upstream's
# docker/Dockerfile.agents.example) are not installed here: install.sh puts their latest releases
# into the persistent home's npm prefix on every start, so they follow `auto_update` too.
COPY run.sh /run.sh
COPY install.sh /opt/paseo-addon/install.sh
# The web UI assets install.sh adds to each installed daemon's bundled web UI.
COPY web/ /opt/paseo-addon/web/
COPY ingress/ /opt/paseo-ingress/
RUN chmod a+x /run.sh /opt/paseo-addon/install.sh

# Replace upstream's entrypoint: run.sh needs root to prepare /data and /share, then execs
# upstream's paseo-docker-entrypoint, which drops to the `paseo` user via gosu.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/run.sh"]
