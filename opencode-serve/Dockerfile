ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.20
FROM ${BUILD_FROM}

# System dependencies
RUN apk add --no-cache \
    bash \
    curl \
    git \
    jq \
    nginx

# Install OpenCode CLI
RUN curl -fsSL https://opencode.ai/install | bash \
    && mv /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && chmod +x /usr/local/bin/opencode

# Install uv/uvx (needed for hass-mcp)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && mv /root/.local/bin/uvx /usr/local/bin/uvx \
    && chmod +x /usr/local/bin/uv /usr/local/bin/uvx

# Remove default nginx config
RUN rm -f /etc/nginx/http.d/default.conf

COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
