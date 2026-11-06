ARG BUILD_FROM

# Bun 1.4.4: the exact release the app is built and tested with (bun:sqlite Database#function and
# Transaction#immediate are required). The official image is only a source for the binary; it is
# pinned by the digest of its multi-arch index, so amd64 and aarch64 both resolve to Bun 1.4.4.
# The binary needs glibc <= 2.17, so it runs on this bookworm base.
FROM oven/bun:1.4.4@sha256:1d22548f177f4ff38e0c15baa3c2c46c6c2060ba2d1bc573bb4720b407548f1d AS bun

# ---------------------------------------------------------------------------------------------
# Build stage: fetch the app at one pinned commit and install its production dependencies.
# ---------------------------------------------------------------------------------------------
FROM ${BUILD_FROM} AS build

# APP_REPO and APP_REF come from build.yaml (APP_REF is a full commit SHA).
ARG APP_REPO
ARG APP_REF

COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun

# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Fetch exactly APP_REF (never a moving branch) and refuse to continue unless HEAD is that commit.
RUN set -eu; \
    case "${APP_REF}" in \
        *[!0-9a-f]*|"") echo "APP_REF must be a full lowercase commit SHA, got '${APP_REF}'" >&2; exit 1 ;; \
    esac; \
    [ "${#APP_REF}" -eq 40 ] || { echo "APP_REF must be 40 characters" >&2; exit 1; }; \
    git init -q .; \
    git remote add origin "${APP_REPO}"; \
    git fetch -q --depth 1 origin "${APP_REF}"; \
    git checkout -q --detach FETCH_HEAD; \
    [ "$(git rev-parse HEAD)" = "${APP_REF}" ]; \
    rm -rf .git; \
    bun install --frozen-lockfile --production; \
    rm -rf /root/.bun

# ---------------------------------------------------------------------------------------------
# Runtime image.
# ---------------------------------------------------------------------------------------------
FROM ${BUILD_FROM}

# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
COPY --from=build /app /app

COPY run.sh /run.sh
COPY show-keys.sh /usr/local/bin/show-keys
RUN chmod +x /run.sh /usr/local/bin/show-keys

CMD ["/run.sh"]
