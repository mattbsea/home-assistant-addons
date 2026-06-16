# Pull the prebuilt fleet-telemetry binary from Tesla's official image (amd64-only).
# The binary is CGO/dynamically linked (glibc + libstdc++); libsodium and ZeroMQ are
# statically linked in, so the runtime base only needs libstdc++6.
FROM tesla/fleet-telemetry:v0.9.0 AS upstream

ARG BUILD_FROM
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
    && rm -rf /var/lib/apt/lists/*

COPY --from=upstream /fleet-telemetry /usr/local/bin/fleet-telemetry

COPY run.sh /run.sh
COPY scripts/ /opt/scripts/
RUN chmod +x /run.sh /opt/scripts/*.sh

CMD ["/run.sh"]
