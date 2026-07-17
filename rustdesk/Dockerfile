# BUILD_FROM must be a global ARG (declared before the first FROM) so the Home Assistant
# builder's --build-arg substitution reaches the runtime stage below. Points at
# lscr.io/linuxserver/rustdesk (see build.yaml) — a browser-accessible RustDesk client streamed
# via Selkies — not one of HA's own base images, since we're layering the RustDesk *server*
# (hbbs/hbbr) on top of the official client image rather than the other way around.
ARG BUILD_FROM
FROM ${BUILD_FROM}

# Pinned upstream rustdesk-server release. Bump deliberately, not automatically.
ARG RUSTDESK_SERVER_VERSION=1.1.15
ARG BUILD_VERSION
ENV RD_ADDON_VERSION=${BUILD_VERSION}

# jq: used by custom-cont-init.d to read Home Assistant's /data/options.json.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        jq \
    && rm -rf /var/lib/apt/lists/*

# rustdesk-server ships hbbs/hbbr as .deb packages, one per Debian architecture (amd64/arm64),
# which map 1:1 onto `dpkg --print-architecture` — no translation table needed. Extract with
# dpkg-deb instead of `dpkg -i`: the packages carry systemd unit files and postinst hooks that
# assume a running init system, and this image's init is s6-overlay, not systemd.
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

# Wire hbbs/hbbr into the base image's own s6-overlay init via its documented extension points
# (https://docs.linuxserver.io/general/container-customization/) instead of replacing the image's
# entrypoint/CMD — the Selkies desktop-streaming stack (Xorg/Wayland, nginx, PulseAudio, etc.)
# still needs to boot normally.
COPY root/ /
RUN chmod +x /custom-cont-init.d/* /custom-services.d/*

# No CMD/ENTRYPOINT: inherit the base image's s6-overlay init.
