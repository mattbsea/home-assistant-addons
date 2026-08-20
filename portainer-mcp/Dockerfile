ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG PORTAINER_MCP_VERSION

# Install Node.js for supergateway.
#
# Debian bookworm's apt "nodejs" package is Node 18, which only exposes the
# global Web Crypto `crypto` object behind the --experimental-global-webcrypto
# flag. supergateway's bundled @modelcontextprotocol/sdk (webStandardStreamableHttp.js)
# references that global directly without importing it, so on plain Node 18
# every session init crashed with "ReferenceError: crypto is not defined".
# Node 19+ exposes it unflagged, so install a current LTS from NodeSource
# instead of the distro package.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install supergateway globally (pinned: --stateful/--sessionTimeout behavior
# relied upon by run.sh was verified against this version)
RUN npm install -g supergateway@3.4.3

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
