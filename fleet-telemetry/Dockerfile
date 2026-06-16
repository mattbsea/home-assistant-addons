# BUILD_FROM must be a global ARG (declared before the first FROM) so the Home Assistant
# builder's --build-arg substitution reaches the runtime stage below.
ARG BUILD_FROM

# Pull the prebuilt fleet-telemetry binary from Tesla's official image (amd64-only).
# The binary is CGO/dynamically linked (glibc + libstdc++); libsodium and ZeroMQ are
# statically linked in, so the runtime base only needs libstdc++6.
FROM tesla/fleet-telemetry:v0.9.0 AS upstream

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
    && rm -rf /var/lib/apt/lists/*

COPY --from=upstream /fleet-telemetry /usr/local/bin/fleet-telemetry

COPY run.sh /run.sh
COPY scripts/ /opt/scripts/
COPY webapp/ /opt/webapp/
RUN chmod +x /run.sh /opt/scripts/*.sh

CMD ["/run.sh"]
