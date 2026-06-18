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


def find_proxy_host(base, tok, domain, http_json=_http_json):
    status, body = http_json("GET", base.rstrip("/") + "/api/nginx/proxy-hosts",
                             headers={"Authorization": "Bearer " + tok})
    if isinstance(body, list):
        d = domain.lower()
        for h in body:
            if d in [n.lower() for n in (h.get("domain_names") or [])]:
                return h.get("id")
    return None


def find_stream(base, tok, incoming_port, http_json=_http_json):
    status, body = http_json("GET", base.rstrip("/") + "/api/nginx/streams",
                             headers={"Authorization": "Bearer " + tok})
    if isinstance(body, list):
        for s in body:
            if int(s.get("incoming_port", -1)) == int(incoming_port):
                return s.get("id")
    return None


def create_pubkey_host(*, base, email, password, domain, forward_host, forward_port, http_json=_http_json):
    """Create (or reuse) the HTTPS proxy host that serves the public key for `domain` (Let's Encrypt
    cert + forward to the add-on's pubkey listener). Returns {ok, id, ...} or {ok: False, error}."""
    base = base.rstrip("/")
    domain = (domain or "").strip().lower()
    if not base or not email or not password:
        return {"ok": False, "error": "NPM url, email and password must be set first."}
    if not domain:
        return {"ok": False, "error": "Set your public-key domain first."}
    if not forward_host:
        return {"ok": False, "error": "Set the HA host IP (forward_host) NPM should forward to."}
    tok, err = token(base, email, password, http_json=http_json)
    if not tok:
        return {"ok": False, "error": err}
    auth = {"Authorization": "Bearer " + tok}
    existing = find_proxy_host(base, tok, domain, http_json=http_json)
    if existing:
        return {"ok": True, "id": existing, "reused": True}
    cstatus, cbody = http_json("POST", base + "/api/nginx/certificates", headers=auth, timeout=120,
                               data={"domain_names": [domain], "provider": "letsencrypt",
                                     "meta": {"letsencrypt_email": email, "letsencrypt_agree": True, "dns_challenge": False}})
    cert_id = cbody.get("id") if isinstance(cbody, dict) else None
    if not cert_id:
        return {"ok": False, "error": f"Let's Encrypt cert request failed (HTTP {cstatus}): {str(cbody)[:300]}. "
                                      f"Confirm {domain} resolves to your public IP and 80/443 reach NPM."}
    hstatus, hbody = http_json("POST", base + "/api/nginx/proxy-hosts", headers=auth, timeout=60, data={
        "domain_names": [domain], "forward_scheme": "http", "forward_host": forward_host,
        "forward_port": forward_port, "certificate_id": cert_id, "ssl_forced": True,
        "block_exploits": True, "caching_enabled": False, "allow_websocket_upgrade": False,
        "access_list_id": 0, "advanced_config": "", "locations": [], "http2_support": False,
        "hsts_enabled": False, "hsts_subdomains": False,
        "meta": {"letsencrypt_agree": False, "dns_challenge": False}})
    host_id = hbody.get("id") if isinstance(hbody, dict) else None
    if not host_id:
        return {"ok": False, "error": f"Proxy host creation failed (HTTP {hstatus}): {str(hbody)[:300]}"}
    return {"ok": True, "id": host_id, "certificate_id": cert_id}


def create_stream(*, base, email, password, incoming_port, forward_host, forward_port, http_json=_http_json):
    """Create (or reuse) a Layer-4 TCP passthrough Stream (no TLS termination — the add-on does mTLS)."""
    base = base.rstrip("/")
    if not base or not email or not password:
        return {"ok": False, "error": "NPM url, email and password must be set first."}
    if not forward_host:
        return {"ok": False, "error": "Set the HA host IP (forward_host) NPM should forward to."}
    tok, err = token(base, email, password, http_json=http_json)
    if not tok:
        return {"ok": False, "error": err}
    auth = {"Authorization": "Bearer " + tok}
    existing = find_stream(base, tok, incoming_port, http_json=http_json)
    if existing:
        return {"ok": True, "id": existing, "reused": True, "incoming_port": incoming_port}
    sstatus, sbody = http_json("POST", base + "/api/nginx/streams", headers=auth, timeout=60, data={
        "incoming_port": incoming_port, "forwarding_host": forward_host, "forwarding_port": forward_port,
        "tcp_forwarding": True, "udp_forwarding": False, "certificate_id": 0, "meta": {}})
    stream_id = sbody.get("id") if isinstance(sbody, dict) else None
    if not stream_id:
        return {"ok": False, "error": f"Stream creation failed (HTTP {sstatus}): {str(sbody)[:300]}. "
                                      f"Confirm port {incoming_port} is forwarded from your router to NPM."}
    return {"ok": True, "id": stream_id, "incoming_port": incoming_port}


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
