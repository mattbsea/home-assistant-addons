ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install system packages and development tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    gosu \
    nano \
    nodejs \
    npm \
    python3 \
    python3-pip \
    python3-requests \
    python3-aiohttp \
    python3-yaml \
    python3-bs4 \
    git \
    vim \
    wget \
    jq \
    tree \
    sudo \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for running the terminal process with sudo access
RUN groupadd --gid 1000 claude \
    && useradd --uid 1000 --gid claude --shell /bin/bash --create-home claude \
    && echo 'claude ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/claude \
    && chmod 0440 /etc/sudoers.d/claude

# Install yq binary (multi-arch)
RUN ARCH=$(dpkg --print-architecture) && \
    case "$ARCH" in \
        amd64) YQ_ARCH="amd64" ;; \
        arm64) YQ_ARCH="arm64" ;; \
        armhf) YQ_ARCH="arm" ;; \
        *) YQ_ARCH="amd64" ;; \
    esac && \
    curl -Lo /usr/local/bin/yq "https://github.com/mikefarah/yq/releases/latest/download/yq_linux_${YQ_ARCH}" && \
    chmod +x /usr/local/bin/yq

# Install uv (provides uvx) - needed by plugins like claude-mem for spawning Python MCP servers
RUN curl -LsSf https://astral.sh/uv/install.sh | UV_UNMANAGED_INSTALL=/usr/local/bin sh

# Copy and build the web terminal application (Express + xterm.js + node-pty)
COPY web-terminal/ /opt/web-terminal/
RUN cd /opt/web-terminal && npm install --production

# Install Claude Code CLI as the claude user so all install files
# (~/.local/bin, ~/.local/lib, etc.) are owned by uid 1000 and accessible
# at runtime without root. A system-wide symlink exposes it on PATH.
USER claude
RUN curl -fsSL https://claude.ai/install.sh | bash
USER root
RUN ln -sf /home/claude/.local/bin/claude /usr/local/bin/claude

# Set working directory to /home/claude (persisted to /data/home at runtime)
WORKDIR /home/claude

# Copy startup script and modular scripts
COPY run.sh /run.sh
COPY scripts/ /opt/scripts/
RUN chmod +x /run.sh \
    && chmod +x /opt/scripts/*.sh

# Use exec form for better signal handling
CMD ["/run.sh"]
