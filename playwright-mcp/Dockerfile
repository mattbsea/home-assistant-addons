ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG PLAYWRIGHT_MCP_VERSION

# Node.js 22 from NodeSource: Debian bookworm's apt nodejs is Node 18, which
# lacks the unflagged global `crypto` that supergateway's MCP SDK needs
# (see portainer-mcp's Dockerfile).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    openssl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# supergateway pinned: --stateful/--sessionTimeout behavior relied on by run.sh
RUN npm install -g supergateway@3.4.3

# Playwright MCP + all three browsers with their system dependencies.
# Browsers live in a fixed path so they are found at runtime.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN mkdir -p /opt/playwright-mcp && cd /opt/playwright-mcp \
    && npm init -y > /dev/null \
    && npm install --no-audit --no-fund "@playwright/mcp@${PLAYWRIGHT_MCP_VERSION}" \
    && ./node_modules/.bin/playwright install --with-deps chromium firefox webkit \
    && rm -rf /var/lib/apt/lists/* /root/.npm

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
