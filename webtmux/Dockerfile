# ARG BUILD_FROM must be declared before the first FROM so it's visible (global scope) to the
# second FROM below, which uses it — the Home Assistant builder's --build-arg substitution only
# reaches ARGs declared at that scope.
ARG BUILD_FROM

# webtmux (https://github.com/chrismccord/webtmux) has no tagged releases — the binaries
# committed to its builds/ directory are just whatever happened to be on main, unpinned. Build
# from source instead, pinned to a specific commit, for a reproducible artifact. GOARCH is left
# to auto-detect: Home Assistant's per-arch build already runs this whole Dockerfile natively (or
# under QEMU) for the target arch, the same way `dpkg --print-architecture` resolves correctly
# elsewhere in this repo without an explicit --platform/TARGETARCH cross-compile setup.
FROM golang:1.23-bookworm AS builder
ARG WEBTMUX_COMMIT=68522482c537a9186648089a7a7c43787c86724e
WORKDIR /src
RUN git clone https://github.com/chrismccord/webtmux.git . \
    && git checkout "${WEBTMUX_COMMIT}"
RUN CGO_ENABLED=0 make build

FROM ${BUILD_FROM}

# tmux: webtmux execs `tmux` as its wrapped command (see run.sh) — it doesn't bundle it.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends tmux \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /src/webtmux /usr/local/bin/webtmux
COPY run.sh /run.sh
RUN chmod a+x /run.sh /usr/local/bin/webtmux

CMD [ "/run.sh" ]
