ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install runtime dependencies for cloning PAI and running the Pulse daemon.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    jq \
    rsync \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install the Bun JavaScript runtime system-wide at /usr/local/bin/bun.
# Pulse (pulse.ts) runs with `bun run`; the dashboard is built with `bun`.
RUN curl -fsSL https://bun.sh/install | BUN_INSTALL=/usr/local bash \
    && bun --version

# Install ttyd (web terminal). It is not packaged in Debian, so the official
# statically linked release binary is used.
RUN ARCH=$(dpkg --print-architecture) && \
    case "${ARCH}" in \
        amd64) TTYD_ARCH="x86_64" ;; \
        arm64) TTYD_ARCH="aarch64" ;; \
        *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;; \
    esac && \
    curl -fsSL -o /usr/local/bin/ttyd \
        "https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.${TTYD_ARCH}" && \
    chmod +x /usr/local/bin/ttyd && \
    ttyd --version

# Install the Claude Code CLI. PAI is driven by Claude Code, so the web
# terminal exposes it for setup tasks such as the `/interview` wizard.
RUN curl -fsSL https://claude.ai/install.sh | bash && \
    ln -sf /root/.local/bin/claude /usr/local/bin/claude && \
    claude --version

# Add-on scripts.
COPY run.sh build-dashboard.sh patch-fetch.js gateway.ts /opt/pai/
RUN chmod +x /opt/pai/run.sh /opt/pai/build-dashboard.sh

CMD ["/opt/pai/run.sh"]
