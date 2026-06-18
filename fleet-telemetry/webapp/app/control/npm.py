"""NGINX Proxy Manager client — single implementation (replaces the duplicate logic that lived in
both scripts/fetch-npm-cert.sh and the v0 server).

This module owns the cert fetch the telemetry server's mTLS needs: authenticate, find the cert for
a domain, download the bundle, and extract the chain + key by CONTENT (NPM's zip member names vary
by version). HTTP is injectable so the orchestration + extraction are unit-testable; the wizard's
cert/host/stream provisioning calls reuse the same token/find helpers.
"""
import io
import json
import os
import urllib.parse
import urllib.request
import zipfile


def _http_json(method, url, headers=None, data=None, timeout=30):
    hdrs = dict(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        try:
            return r.status, json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return r.status, raw.decode("utf-8", "replace")


def _http_get_bytes(url, token, timeout=120):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def token(base, email, password, http_json=_http_json):
    base = base.rstrip("/")
    status, body = http_json("POST", base + "/api/tokens", data={"identity": email, "secret": password})
    if isinstance(body, dict) and body.get("token"):
        return body["token"], None
    return None, f"NPM auth failed (HTTP {status}): {str(body)[:200]}"


def find_cert_id(base, tok, domain, http_json=_http_json):
    status, body = http_json("GET", base.rstrip("/") + "/api/nginx/certificates",
                             headers={"Authorization": "Bearer " + tok})
    d = domain.strip().lower()
    if isinstance(body, list):
        for c in body:
            names = [n.lower() for n in (c.get("domain_names") or [])]
            if d in names or (c.get("nice_name") or "").lower() == d:
                return c.get("id")
    return None


def extract_cert(zip_bytes):
    """Return (fullchain_pem, privkey_pem) bytes from an NPM cert bundle, classifying by content:
    the private key is the member with a PRIVATE KEY block; the chain is the member with the most
    CERTIFICATE blocks (leaf + intermediates)."""
    privkey = fullchain = None
    best = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            data = z.read(name)
            text = data.decode("utf-8", "replace")
            if "PRIVATE KEY" in text:
                privkey = data
            count = text.count("BEGIN CERTIFICATE")
            if count > best:
                best, fullchain = count, data
    if not fullchain or not privkey:
        raise ValueError("bundle missing certificate chain or private key")
    return fullchain, privkey


def fetch_cert(base, email, password, domain, certs_dir, *, http_json=_http_json, get_bytes=_http_get_bytes):
    """Authenticate, locate the cert for `domain`, download + extract it, and install
    server.crt/server.key (mode 600) into certs_dir. Returns {"ok": bool, "error"?}."""
    base = base.rstrip("/")
    tok, err = token(base, email, password, http_json=http_json)
    if not tok:
        return {"ok": False, "error": err}
    cert_id = find_cert_id(base, tok, domain, http_json=http_json)
    if not cert_id:
        return {"ok": False, "error": f"no NPM certificate found for domain '{domain}'"}
    try:
        zip_bytes = get_bytes(base + f"/api/nginx/certificates/{cert_id}/download", tok)
        chain, key = extract_cert(zip_bytes)
    except Exception as e:
        return {"ok": False, "error": f"certificate download/extract failed: {e}"}
    os.makedirs(certs_dir, exist_ok=True)
    for name, data in (("server.crt", chain), ("server.key", key)):
        path = os.path.join(certs_dir, name)
        with open(path + ".new", "wb") as fh:
            fh.write(data)
        os.chmod(path + ".new", 0o600)
        os.replace(path + ".new", path)
    return {"ok": True, "certificate_id": cert_id}
