#!/usr/bin/env python3
"""CLI shim so run.sh can fetch+install the NPM mTLS cert via the single Python NPM client
(replaces scripts/fetch-npm-cert.sh). Reads NPM_* / CERTS_DIR from the env; exits 0 on success."""
import os
import sys

from app.control import npm


def main():
    r = npm.fetch_cert(os.environ.get("NPM_URL", ""), os.environ.get("NPM_EMAIL", ""),
                       os.environ.get("NPM_PASSWORD", ""), os.environ.get("NPM_CERT_DOMAIN", ""),
                       os.environ.get("CERTS_DIR", "/data/certs"))
    if r.get("ok"):
        print(f"[fetch-npm-cert] installed certificate id {r.get('certificate_id')}", flush=True)
        return 0
    print(f"[fetch-npm-cert] ERROR: {r.get('error')}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
