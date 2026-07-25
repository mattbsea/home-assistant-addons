ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.20
FROM ${BUILD_FROM}

RUN apk add --no-cache \
    bash \
    curl \
    git \
    jq \
    nginx

RUN curl -fsSL https://opencode.ai/install | bash \
    && mv /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && chmod +x /usr/local/bin/opencode

RUN rm -f /etc/nginx/http.d/default.conf

COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
