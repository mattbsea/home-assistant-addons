# BUILD_FROM is the official Paseo image (see build.yaml): Debian bookworm + Node 22, the
# daemon/CLI, the bundled web UI, and a non-root `paseo` user (uid 1000). It has no bashio,
# so run.sh reads /data/options.json with jq and talks to Supervisor with curl directly.
ARG BUILD_FROM
FROM ${BUILD_FROM}

USER root

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends jq ripgrep unzip \
      python3 python3-venv python3-pip python-is-python3 openssh-client \
      wget nano vim tree build-essential; \
    rm -rf /var/lib/apt/lists/*

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

# uv, Bun and the agent CLIs below install their latest release. Supervisor passes the add-on
# version as BUILD_VERSION; declaring it here invalidates the build cache from this point on, so
# every add-on update fetches fresh versions instead of reusing cached layers.
ARG BUILD_VERSION

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

# Agent CLIs Paseo drives (same set as upstream's docker/Dockerfile.agents.example), at their
# latest versions when the image is built.
RUN set -eux; \
    npm install -g \
      "@anthropic-ai/claude-code@latest" \
      "@openai/codex@latest" \
      "opencode-ai@latest"; \
    npm cache clean --force; \
    claude --version; \
    codex --version; \
    opencode --version

COPY run.sh /run.sh
COPY ingress/ /opt/paseo-ingress/
RUN chmod a+x /run.sh

# Make the bundled web UI configure itself for this daemon: add the setup page next to the app
# and a small redirect to it (only when the browser has no saved hosts) at the top of <head>.
# The daemon still injects its own connection hint before </head> at request time.
COPY web/ /tmp/paseo-web/
RUN set -eux; \
    web_ui="$(npm root -g)/@getpaseo/server/dist/server/web-ui"; \
    test -f "${web_ui}/index.html"; \
    cp /tmp/paseo-web/paseo-setup.html "${web_ui}/paseo-setup.html"; \
    rm -f "${web_ui}/paseo-setup.html.gz" "${web_ui}/paseo-setup.html.br" \
          "${web_ui}/index.html.gz" "${web_ui}/index.html.br"; \
    snippet="$(tr -d '\n' < /tmp/paseo-web/index-redirect.html)"; \
    SNIPPET="${snippet}" node -e 'const fs=require("fs");const f=process.argv[1];const h=fs.readFileSync(f,"utf8");if(!/<head>/i.test(h))process.exit(1);fs.writeFileSync(f,h.replace(/<head>/i,m=>m+process.env.SNIPPET));' "${web_ui}/index.html"; \
    grep -q 'paseo-setup.html' "${web_ui}/index.html"; \
    rm -rf /tmp/paseo-web

# Replace upstream's entrypoint: run.sh needs root to prepare /data and /share, then execs
# upstream's paseo-docker-entrypoint, which drops to the `paseo` user via gosu.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/run.sh"]
