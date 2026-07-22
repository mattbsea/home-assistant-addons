ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install OmniRoute globally
RUN \
    apk add --no-cache \
        git \
        nodejs \
        npm \
    && npm install -g omniroute@3.8.48 \
    && apk del git

# Copy entrypoint
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
