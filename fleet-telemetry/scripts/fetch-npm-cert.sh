#!/usr/bin/env bash
# Fetch a Let's Encrypt certificate from Nginx Proxy Manager's REST API and write it to
# <CERTS_DIR>/server.crt and server.key (chmod 600). NPM still issues/renews the cert via LE;
# this just pulls the current PEMs so fleet-telemetry can terminate mTLS itself.
#
# Reads configuration from the environment (keeps secrets out of `ps`):
#   NPM_URL          base URL of the NPM admin API, e.g. https://proxy.example.org:81
#   NPM_EMAIL        NPM login email
#   NPM_PASSWORD     NPM login password
#   NPM_CERT_DOMAIN  domain whose certificate to fetch, e.g. telemetry.example.org
#   CERTS_DIR        output directory (default /data/certs)
#
# Exit codes: 0 = certs written; non-zero = failure (caller decides fatal vs. use-cached).

set -o pipefail

CERTS_DIR="${CERTS_DIR:-/data/certs}"
log() { echo "[fetch-npm-cert] $*" >&2; }

for var in NPM_URL NPM_EMAIL NPM_PASSWORD NPM_CERT_DOMAIN; do
    if [ -z "${!var:-}" ]; then
        log "ERROR: ${var} is not set"
        exit 2
    fi
done

BASE_URL="${NPM_URL%/}"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

# 1. Authenticate -> JWT
TOKEN="$(curl -fsS -X POST "${BASE_URL}/api/tokens" \
    -H 'Content-Type: application/json' \
    --data "$(jq -n --arg i "${NPM_EMAIL}" --arg s "${NPM_PASSWORD}" '{identity:$i, secret:$s}')" \
    | jq -r '.token // empty')"
if [ -z "${TOKEN}" ]; then
    log "ERROR: authentication to NPM failed (no token returned)"
    exit 3
fi

# 2. Resolve the certificate id by domain name
CERTS_JSON="$(curl -fsS "${BASE_URL}/api/nginx/certificates" -H "Authorization: Bearer ${TOKEN}")"
# A non-admin NPM user only sees certificates it owns, so an empty list usually means the
# configured account lacks the rights to see the cert (not that the cert is missing).
CERT_COUNT="$(echo "${CERTS_JSON}" | jq 'length' 2>/dev/null || echo 0)"
if [ "${CERT_COUNT:-0}" -eq 0 ]; then
    log "ERROR: the NPM account '${NPM_EMAIL}' can see 0 certificates."
    log "       In NPM, non-admin users only see certificates they own. Use an NPM"
    log "       administrator account (or one that owns the cert) in npm_email/npm_password."
    exit 4
fi
# Match the domain case-insensitively — LE/NPM store domains lowercase, but the user may type
# mixed case in the add-on config. Pick the newest matching cert if several exist.
CERT_ID="$(echo "${CERTS_JSON}" \
    | jq -r --arg d "${NPM_CERT_DOMAIN}" \
        '[.[] | select(.domain_names | map(ascii_downcase) | index($d | ascii_downcase))] | sort_by(.expires_on) | last | .id // empty')"
if [ -z "${CERT_ID}" ]; then
    log "ERROR: no NPM certificate found for domain '${NPM_CERT_DOMAIN}' (account sees ${CERT_COUNT} cert(s), none match)."
    exit 4
fi
log "Using NPM certificate id ${CERT_ID} for ${NPM_CERT_DOMAIN}"

# 3. Download the certificate bundle (zip)
ZIP="${TMPDIR}/cert.zip"
if ! curl -fsS "${BASE_URL}/api/nginx/certificates/${CERT_ID}/download" \
        -H "Authorization: Bearer ${TOKEN}" -o "${ZIP}"; then
    log "ERROR: certificate download failed"
    exit 5
fi

# 4. Extract and classify by CONTENT, not filename (NPM's zip member names vary by version/
#    cert type). The private key is the file containing a PRIVATE KEY block; the server cert is
#    the PEM with the most CERTIFICATE blocks (i.e. the full chain: leaf + intermediates).
EXTRACT="${TMPDIR}/extract"
mkdir -p "${EXTRACT}"
if ! unzip -o -q "${ZIP}" -d "${EXTRACT}"; then
    log "ERROR: downloaded bundle is not a valid zip"
    exit 6
fi

PRIVKEY=""
FULLCHAIN=""
BEST_CERT_COUNT=0
while IFS= read -r f; do
    if grep -q "PRIVATE KEY" "${f}" 2>/dev/null; then
        PRIVKEY="${f}"
    fi
    count="$(grep -c "BEGIN CERTIFICATE" "${f}" 2>/dev/null)"
    if [ "${count:-0}" -gt "${BEST_CERT_COUNT}" ]; then
        BEST_CERT_COUNT="${count}"
        FULLCHAIN="${f}"
    fi
done < <(find "${EXTRACT}" -type f)

if [ -z "${FULLCHAIN}" ] || [ -z "${PRIVKEY}" ]; then
    log "ERROR: could not locate a certificate chain and private key in the bundle. Contents:"
    find "${EXTRACT}" -type f -printf '  %P\n' >&2
    exit 7
fi

# Sanity-check the PEMs before installing them
if ! openssl x509 -in "${FULLCHAIN}" -noout >/dev/null 2>&1; then
    log "ERROR: selected chain file is not a valid certificate"
    exit 8
fi

# 5. Install atomically with locked-down permissions
mkdir -p "${CERTS_DIR}"
cp "${FULLCHAIN}" "${CERTS_DIR}/server.crt.new"
cp "${PRIVKEY}"  "${CERTS_DIR}/server.key.new"
chmod 600 "${CERTS_DIR}/server.crt.new" "${CERTS_DIR}/server.key.new"
mv "${CERTS_DIR}/server.crt.new" "${CERTS_DIR}/server.crt"
mv "${CERTS_DIR}/server.key.new" "${CERTS_DIR}/server.key"

SUBJECT="$(openssl x509 -in "${CERTS_DIR}/server.crt" -noout -subject 2>/dev/null)"
log "Installed certificate (${SUBJECT})"
exit 0
