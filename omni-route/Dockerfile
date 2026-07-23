ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install OmniRoute globally
RUN \
    apk add --no-cache \
        curl \
        git \
        nodejs \
        npm \
    && npm install -g omniroute@3.8.48 http-proxy@1.18.1 \
    && apk del git

# Copy entrypoint and ingress-redirect proxy
COPY run.sh /run.sh
COPY scripts/ingress-proxy.js /opt/scripts/ingress-proxy.js
RUN chmod a+x /run.sh

CMD ["/run.sh"]
