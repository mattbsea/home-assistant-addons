# BUILD_FROM is the official Paseo image (see build.yaml): Debian bookworm + Node 22, the
# daemon/CLI, the bundled web UI, and a non-root `paseo` user (uid 1000). It has no bashio,
# so run.sh reads /data/options.json with jq and talks to Supervisor with curl directly.
ARG BUILD_FROM
FROM ${BUILD_FROM}

USER root

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends jq ripgrep; \
    rm -rf /var/lib/apt/lists/*

# Agent CLIs Paseo drives. Same set as upstream's docker/Dockerfile.agents.example, pinned so a
# rebuild on the HA host is reproducible; bump these (and the add-on version) to upgrade.
ARG CLAUDE_CODE_VERSION=2.1.281
ARG CODEX_VERSION=0.156.1
ARG OPENCODE_VERSION=1.18.32
RUN set -eux; \
    npm install -g \
      "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
      "@openai/codex@${CODEX_VERSION}" \
      "opencode-ai@${OPENCODE_VERSION}"; \
    npm cache clean --force; \
    claude --version; \
    codex --version; \
    opencode --version

COPY run.sh /run.sh
COPY ingress/ /opt/paseo-ingress/
RUN chmod a+x /run.sh

# Replace upstream's entrypoint: run.sh needs root to prepare /data and /share, then execs
# upstream's paseo-docker-entrypoint, which drops to the `paseo` user via gosu.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/run.sh"]
