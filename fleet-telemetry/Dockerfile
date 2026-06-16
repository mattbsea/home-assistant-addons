# BUILD_FROM must be a global ARG (declared before the first FROM) so the Home Assistant
# builder's --build-arg substitution reaches the runtime stage below.
ARG BUILD_FROM

# Pull the prebuilt fleet-telemetry binary from Tesla's official image (amd64-only).
# The binary is CGO/dynamically linked (glibc + libstdc++); libsodium and ZeroMQ are
# statically linked in, so the runtime base only needs libstdc++6.
FROM tesla/fleet-telemetry:v0.9.0 AS upstream

# MyTeslaMate websocket server (bundled, optional). We copy the already-installed app from the
# published image — its node_modules (express/express-ws/ws/sync-request) are pure JS, so they run
# fine under the Node runtime we install on the Debian base. Pinned by digest for reproducibility.
FROM myteslamate/websocket@sha256:bbe99c9315eb3147c40353511f3ac17fcc95421c231c3a90f6c0b7886102c86a AS teslamate_ws

FROM ${BUILD_FROM}

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libstdc++6 \
        ca-certificates \
        curl \
        jq \
        unzip \
        openssl \
        python3 \
        nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY --from=upstream /fleet-telemetry /usr/local/bin/fleet-telemetry
COPY --from=teslamate_ws /usr/node/app /opt/teslamate-ws

COPY run.sh /run.sh
COPY scripts/ /opt/scripts/
COPY webapp/ /opt/webapp/
RUN chmod +x /run.sh /opt/scripts/*.sh

CMD ["/run.sh"]
