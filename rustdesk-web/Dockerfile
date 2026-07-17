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
# nginx: fronts apimain on the real ingress-facing port and rewrites its one hardcoded,
# prefix-unaware redirect (see nginx.conf) -- apimain itself has no reverse-proxy-prefix support.
RUN apk add --no-cache jq nginx

COPY run.sh /run.sh
COPY nginx.conf /nginx.conf
RUN chmod a+x /run.sh

# The compiled admin SPA hardcodes its API calls to the domain-absolute path "/api/admin"
# (rustdesk-api-web's src/utils/request.js: baseURL: import.meta.env.VITE_SERVER_API, baked in
# at their build time as "/api/admin"). That's blind to any reverse-proxy path prefix: under HA
# ingress the browser's JS ends up requesting <ha-host>/api/admin/... directly -- bypassing the
# ingress proxy (and this container) entirely, since that path doesn't start with the ingress
# token prefix. The result is a login API call that never reaches anything, so the admin panel
# hangs on an infinite loading spinner (confirmed live, screenshot from a real device). Unlike
# the "/" redirect (fixed via nginx.conf's proxy_redirect, a server-side response header), this
# is the browser's own JS constructing a URL client-side, so no server-side rewrite can fix it --
# the fix has to change what URL the compiled JS itself requests. Patch it to compute the actual
# prefix from the current page's own path at runtime instead: strip everything from "/_admin/"
# onward (giving the ingress prefix, or "" when accessed directly without one) and append
# "/api/admin" -- correct in both cases, no X-Ingress-Path awareness needed since this runs
# entirely client-side. Fails the build loudly rather than silently no-op if upstream ever
# changes this exact string, since a silent no-op here means the spinner bug comes back.
RUN set -eu; \
    MATCHES="$(grep -rl 'baseURL:"/api/admin"' /app/resources/admin/)"; \
    test -n "${MATCHES}"; \
    for f in ${MATCHES}; do \
        sed -i 's|baseURL:"/api/admin"|baseURL:(location.pathname.split("/_admin/")[0]+"/api/admin")|' "${f}"; \
    done

WORKDIR /app
CMD [ "/run.sh" ]
