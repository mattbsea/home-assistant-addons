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
    openssh-client \
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
    unzip \
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

# Install the latest stable Python via uv and put it ahead of apt's python3 on PATH.
# Debian bookworm's apt python3 stays installed (at /usr/bin) for any OS-level tooling
# that expects Debian's own interpreter; this uv-managed build is what `python3`/`python`
# resolve to for interactive/dev use since /usr/local/bin precedes /usr/bin on PATH.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
RUN uv python install 3.14 \
    && ln -sf "$(uv python find 3.14)" /usr/local/bin/python3 \
    && ln -sf /usr/local/bin/python3 /usr/local/bin/python \
    && chmod -R a+rX /opt/uv/python

# Install bun (fast JavaScript runtime / package manager).
# Bun publishes prebuilt binaries for amd64 and aarch64 only; armv7 is unsupported,
# so skip the install there rather than failing the build.
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "armhf" ]; then \
        echo "Skipping bun install: armv7 is not supported by bun"; \
    else \
        curl -fsSL https://bun.sh/install | BUN_INSTALL=/usr/local bash; \
    fi

# Install nvm and Node.js 24 as claude user at build time.
# node-pty must be compiled against the same Node.js version used at runtime.
USER claude
RUN curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/master/install.sh | bash \
    && export NVM_DIR="/home/claude/.nvm" \
    && . "$NVM_DIR/nvm.sh" \
    && nvm install 24
USER root

# Copy and build the web terminal application (Express + xterm.js + node-pty)
# Uses nvm's Node 24 so node-pty native addon matches runtime Node version.
COPY web-terminal/ /opt/web-terminal/
RUN bash -c 'source /home/claude/.nvm/nvm.sh && cd /opt/web-terminal && npm install --production'

# Install Claude Code CLI as the claude user so all install files
# (~/.local/bin, ~/.local/lib, etc.) are owned by uid 1000 and accessible
# at runtime without root. A system-wide symlink exposes it on PATH.
# The CLI is also preserved at /opt/claude-cli/ to survive the /home/claude
# symlink replacement at runtime.
USER claude
RUN curl -fsSL https://claude.ai/install.sh | bash
USER root
RUN ln -sf /home/claude/.local/bin/claude /usr/local/bin/claude \
    && cp -a /home/claude/.local /opt/claude-cli

# Install OpenCode CLI as the claude user alongside Claude Code.
# Uses XDG_BIN_DIR to place the binary in ~/.local/bin (same as Claude).
# Preserved at /opt/opencode-cli/ to survive the /home/claude symlink replacement.
USER claude
RUN XDG_BIN_DIR=/home/claude/.local/bin curl -fsSL https://opencode.ai/install | bash
USER root
RUN ln -sf /home/claude/.local/bin/opencode /usr/local/bin/opencode \
    && cp -a /home/claude/.opencode /opt/opencode-cli 2>/dev/null || true

# Configure claude-mem to use OpenRouter for the observation agent
ENV CLAUDE_MEM_PROVIDER=openrouter \
    CLAUDE_MEM_OPENROUTER_MODEL=xiaomi/mimo-v2-flash:free \
    CLAUDE_MEM_OPENROUTER_APP_NAME=claude-mem

# Set working directory to /home/claude (persisted to /data/home at runtime)
WORKDIR /home/claude

# Copy startup script and modular scripts
COPY run.sh /run.sh
COPY scripts/ /opt/scripts/
RUN chmod +x /run.sh \
    && chmod +x /opt/scripts/*.sh

# Use exec form for better signal handling
CMD ["/run.sh"]
