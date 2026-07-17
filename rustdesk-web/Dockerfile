# BUILD_FROM must be a global ARG (declared before the first FROM) so the Home Assistant
# builder's --build-arg substitution reaches the runtime stage below. Points at
# lejianwen/rustdesk-api (see build.yaml) — a lightweight web-admin + browser RustDesk client
# that talks to an external hbbs/hbbr server over the env vars run.sh sets, rather than
# bundling its own (the `:full-s6` variant of this image does bundle one; we deliberately don't
# use it, since the rustdesk-server add-on already provides that role).
ARG BUILD_FROM
FROM ${BUILD_FROM}

# jq: run.sh reads Home Assistant's /data/options.json. This base image has no bashio (that's
# specific to Home Assistant's own base images; this one is lejianwen's plain Alpine image), so
# config has to be read manually, same as this repo's old rustdesk add-on did before bashio was
# an option there either.
RUN apk add --no-cache jq

COPY run.sh /run.sh
RUN chmod a+x /run.sh

WORKDIR /app
CMD [ "/run.sh" ]
