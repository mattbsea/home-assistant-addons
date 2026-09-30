ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG PASEO_HUB_VERSION

# Node.js 22 from NodeSource: bookworm's apt nodejs is Node 18 (see playwright-mcp's Dockerfile).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g --no-audit --no-fund "@getpaseo/hub@${PASEO_HUB_VERSION}" \
    && rm -rf /root/.npm

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
