# BUILD_FROM must be a global ARG (declared before the first FROM) so the Home Assistant
# builder's --build-arg substitution reaches the runtime stage below.
ARG BUILD_FROM
FROM ${BUILD_FROM}

# Pinned upstream rustdesk-server release. Bump deliberately, not automatically.
ARG RUSTDESK_SERVER_VERSION=1.1.15
ARG BUILD_VERSION
ENV RD_ADDON_VERSION=${BUILD_VERSION}

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        jq \
        python3 \
        python3-starlette \
        python3-uvicorn \
    && rm -rf /var/lib/apt/lists/*

# rustdesk-server ships hbbs/hbbr as .deb packages, one per Debian architecture
# (amd64/arm64/armhf), which map 1:1 onto `dpkg --print-architecture` — no translation table
# needed. Extract with dpkg-deb instead of `dpkg -i`: the packages carry systemd unit files and
# postinst hooks that assume a running init system, neither of which exists in this image.
RUN set -eu; \
    DEB_ARCH="$(dpkg --print-architecture)"; \
    TMP="$(mktemp -d)"; \
    for pkg in hbbs hbbr; do \
        url="https://github.com/rustdesk/rustdesk-server/releases/download/${RUSTDESK_SERVER_VERSION}/rustdesk-server-${pkg}_${RUSTDESK_SERVER_VERSION}_${DEB_ARCH}.deb"; \
        curl -fsSL -o "${TMP}/${pkg}.deb" "${url}"; \
        dpkg-deb -x "${TMP}/${pkg}.deb" "${TMP}/${pkg}"; \
        install -m 0755 "${TMP}/${pkg}/usr/bin/${pkg}" "/usr/local/bin/${pkg}"; \
    done; \
    rm -rf "${TMP}"; \
    test -x /usr/local/bin/hbbs; \
    test -x /usr/local/bin/hbbr

COPY run.sh /run.sh
COPY webapp/ /opt/webapp/
RUN chmod +x /run.sh

CMD ["/run.sh"]
