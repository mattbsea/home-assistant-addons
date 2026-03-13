ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install system packages and development tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
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
    tmux \
    && rm -rf /var/lib/apt/lists/*

# Install ttyd binary (not in Debian repos, use GitHub release)
RUN ARCH=$(dpkg --print-architecture) && \
    case "$ARCH" in \
        amd64)  TTYD_ARCH="x86_64" ;; \
        arm64)  TTYD_ARCH="aarch64" ;; \
        armhf)  TTYD_ARCH="armv7l" ;; \
        *)      TTYD_ARCH="x86_64" ;; \
    esac && \
    TTYD_VER=$(curl -fsSL https://api.github.com/repos/tsl0922/ttyd/releases/latest | grep '"tag_name"' | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/') && \
    curl -fsSL "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VER}/ttyd.${TTYD_ARCH}" -o /usr/local/bin/ttyd && \
    chmod +x /usr/local/bin/ttyd

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

# Install Claude Code CLI using native installer
# The installer places the binary in ~/.local/bin
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && ln -sf /root/.local/bin/claude /usr/local/bin/claude

# Set working directory to data (only mounted volume at runtime)
WORKDIR /data

# Copy startup script and modular scripts
COPY run.sh /run.sh
COPY scripts/ /opt/scripts/
RUN chmod +x /run.sh \
    && chmod +x /opt/scripts/*.sh

# Use exec form for better signal handling
CMD ["/run.sh"]
