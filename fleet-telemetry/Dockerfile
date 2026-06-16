# BUILD_FROM must be a global ARG (declared before the first FROM) so the Home Assistant
# builder's --build-arg substitution reaches the runtime stage below.
ARG BUILD_FROM

# Pull the prebuilt fleet-telemetry binary from Tesla's official image (amd64-only).
# The binary is CGO/dynamically linked (glibc + libstdc++); libsodium and ZeroMQ are
# statically linked in, so the runtime base only needs libstdc++6.
FROM tesla/fleet-telemetry:v0.9.0 AS upstream

# MyTeslaMate websocket server (bundled, optional). We vendor the upstream "master" source — the
# HTTP-receiver variant (POST / accepts the Pub/Sub-push envelope; NO google-cloud subscriber) — and
# npm-install its pure-JS deps here. The published Docker image is the Pub/Sub-pull variant and is
# deliberately NOT used. node_modules built here run under the add-on's Node runtime (both glibc).
FROM node:20-bookworm-slim AS teslamate_ws
WORKDIR /opt/teslamate-ws
COPY teslamate-ws/package*.json ./
RUN npm install --omit=dev
COPY teslamate-ws/ ./

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
