ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG PORTAINER_MCP_VERSION

# Install Node.js for supergateway
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install supergateway globally
RUN npm install -g supergateway

# Map HA arch to portainer-mcp release arch
RUN case "${BUILD_ARCH}" in \
        aarch64) ARCH="arm64" ;; \
        amd64)   ARCH="amd64" ;; \
        *) echo "Unsupported arch: ${BUILD_ARCH}" && exit 1 ;; \
    esac && \
    curl -fsSL \
        "https://github.com/portainer/portainer-mcp/releases/download/v${PORTAINER_MCP_VERSION}/portainer-mcp-v${PORTAINER_MCP_VERSION}-linux-${ARCH}.tar.gz" \
        -o /tmp/portainer-mcp.tar.gz && \
    tar -xzf /tmp/portainer-mcp.tar.gz -C /usr/local/bin/ portainer-mcp && \
    chmod +x /usr/local/bin/portainer-mcp && \
    rm /tmp/portainer-mcp.tar.gz

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
