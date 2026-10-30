# BUILD_FROM must be a global ARG (declared before the first FROM) so the Home Assistant
# builder's --build-arg substitution reaches the runtime stage below.
ARG BUILD_FROM

# Build the fleet-telemetry binary from source, pinned to an upstream commit. The newest tag (v0.9.5)
# has Field enum entries through 271 but not 272-277 (AccessoryRail, PowerTransferStatus,
# AutomaticEmergencyBrakingState, RollingResistanceCoefficient, LifetimeDcChargeEnergyKwh, ...), which
# first landed in commit 18800f77 ("Add Semi streaming fields", 2026-10-26, v0.9.5 + 1). The logger
# names fields via protos.Field_name, so a binary built from an older proto silently drops them. That
# commit also adds the AEB / power-transfer-status value decoders. Mirrors upstream's own Dockerfile
# (Go 1.27 per its go.mod). The binary is CGO/dynamically linked (glibc + libstdc++); libsodium and
# ZeroMQ are statically linked in, so the runtime base only needs libstdc++6. Switch back to a
# tesla/fleet-telemetry:vX image once a release contains 18800f77.
FROM golang:1.27-bookworm AS upstream
ARG FT_COMMIT=18800f7738757a741479e71b38ece584d363ef2d
WORKDIR /build
RUN wget -q https://github.com/jedisct1/libsodium/releases/download/1.0.19-RELEASE/libsodium-1.0.19.tar.gz \
    && tar -xzf libsodium-1.0.19.tar.gz \
    && cd libsodium-stable \
    && ./configure --disable-shared --enable-static \
    && make -j"$(nproc)" && make install
RUN wget -q https://github.com/zeromq/libzmq/releases/download/v4.3.4/zeromq-4.3.4.tar.gz \
    && tar -xzf zeromq-4.3.4.tar.gz \
    && cd zeromq-4.3.4 \
    && ./configure --enable-static --disable-shared --disable-Werror \
    && make -j"$(nproc)" && make install
WORKDIR /go/src/fleet-telemetry
RUN wget -qO- "https://github.com/teslamotors/fleet-telemetry/archive/${FT_COMMIT}.tar.gz" \
        | tar -xz --strip-components=1
ENV CGO_ENABLED=1
ENV CGO_LDFLAGS="-lstdc++"
RUN make build && cp /go/bin/fleet-telemetry /fleet-telemetry

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
        python3-starlette \
        python3-uvicorn \
    && rm -rf /var/lib/apt/lists/*

COPY --from=upstream /fleet-telemetry /usr/local/bin/fleet-telemetry
COPY --from=proxy_builder /tesla-http-proxy /usr/local/bin/tesla-http-proxy

COPY run.sh /run.sh
COPY webapp/ /opt/webapp/
RUN chmod +x /run.sh

CMD ["/run.sh"]
