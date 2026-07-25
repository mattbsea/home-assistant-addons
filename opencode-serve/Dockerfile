ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_ARCH

# Install system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install OpenCode globally via npm (pinned version)
RUN npm install -g opencode-ai@1.18.5

# Create working directory
RUN mkdir -p /data/workspace

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
