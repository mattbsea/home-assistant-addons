# BUILD_FROM must be a global ARG (declared before the first FROM) so the Home Assistant
# builder's --build-arg substitution reaches the runtime stage below.
ARG BUILD_FROM

# Pull the prebuilt fleet-telemetry binary from Tesla's official image (amd64-only).
# The binary is CGO/dynamically linked (glibc + libstdc++); libsodium and ZeroMQ are
# statically linked in, so the runtime base only needs libstdc++6.
FROM tesla/fleet-telemetry:v0.9.0 AS upstream

# Build tesla-http-proxy from source (vehicle-command v0.4.1). The binary acts as a local signing
# proxy: it inserts JWS signatures into fleet_telemetry_config (and other signed-command endpoints)
# using the app's EC private key, so we never need to implement JWS signing ourselves.
FROM golang:1.23-alpine AS proxy_builder
RUN apk add --no-cache git
RUN git clone --depth 1 --branch v0.4.1 \
        https://github.com/teslamotors/vehicle-command /src
WORKDIR /src
RUN go build -o /tesla-http-proxy ./cmd/tesla-http-proxy

FROM ${BUILD_FROM}

# Supervisor passes the add-on version as a build arg; surface it to the dashboard.
ARG BUILD_VERSION
ENV FT_ADDON_VERSION=${BUILD_VERSION}

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
        python3-websockets \
    && rm -rf /var/lib/apt/lists/*

COPY --from=upstream /fleet-telemetry /usr/local/bin/fleet-telemetry
COPY --from=proxy_builder /tesla-http-proxy /usr/local/bin/tesla-http-proxy

COPY run.sh /run.sh
COPY scripts/ /opt/scripts/
COPY webapp/ /opt/webapp/
RUN chmod +x /run.sh /opt/scripts/*.sh

CMD ["/run.sh"]
