ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install runtime dependencies for cloning PAI and running the Pulse daemon
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    jq \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install the Bun JavaScript runtime system-wide at /usr/local/bin/bun.
# Pulse (pulse.ts) is executed with `bun run`.
RUN curl -fsSL https://bun.sh/install | BUN_INSTALL=/usr/local bash \
    && bun --version

# Startup script
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
