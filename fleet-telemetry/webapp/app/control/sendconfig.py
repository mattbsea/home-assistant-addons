"""Send the signed fleet_telemetry_config to the vehicle(s).

fleet_telemetry_config is a signed vehicle command: it needs the app EC private key, which
tesla-http-proxy injects as a JWS signature (a plain Bearer POST returns 404). Ported from the v0
server. The pure payload helpers are unit-tested; the proxy orchestration is verified at cutover.
"""
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

import fields


def extract_ca_chain(cert_pem):
    """The intermediate chain (every cert after the leaf) from a fullchain PEM, joined; '' if none."""
    certs = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", cert_pem, re.DOTALL)
    return "\n".join(certs[1:]) if len(certs) > 1 else ""


def build_request(domain, port, ca_chain, vins, roster=None):
    """The {vins, config} body POSTed to fleet_telemetry_config."""
    roster = fields.TELEMETRY_FIELDS if roster is None else roster
    return {"vins": vins, "config": {"hostname": domain, "port": port, "ca": ca_chain, "fields": roster}}


def _refresh_access_token(client_id, refresh_token, auth_host):
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "client_id": client_id,
                                   "refresh_token": refresh_token}).encode()
    req = urllib.request.Request(auth_host + "/oauth2/v3/token", data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def send(*, vins, client_id, refresh_token, domain, region, port=4443, cert_file, private_key_file,
         proxy_bin="/usr/local/bin/tesla-http-proxy", auth_host="https://auth.tesla.com"):
    """Sign and POST fleet_telemetry_config for `vins`. Returns {ok, response} or {ok: False, error}.
    `on_token` rotation is the caller's concern (it owns persistence)."""
    if not client_id:
        return {"ok": False, "error": "Tesla client_id is not set"}
    if not refresh_token:
        return {"ok": False, "error": "No refresh token available — complete the OAuth login step first"}
    if not vins:
        return {"ok": False, "error": "No VINs — wait for telemetry/priming, then retry"}
    if not os.path.exists(private_key_file):
        return {"ok": False, "error": f"App private key not found at {private_key_file} — generate the keypair first"}

    try:
        tok = _refresh_access_token(client_id, refresh_token, auth_host)
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"Token request failed HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"Token request failed: {e}"}
    at = tok.get("access_token")
    if not at:
        return {"ok": False, "error": "No access_token returned"}
    new_rt = tok.get("refresh_token") if tok.get("refresh_token") != refresh_token else None

    ca_chain = ""
    try:
        with open(cert_file) as fh:
            ca_chain = extract_ca_chain(fh.read())
    except OSError:
        pass
    payload = json.dumps(build_request(domain, port, ca_chain, vins)).encode()

    tmpdir = __import__("tempfile").mkdtemp(prefix="ft-proxy-")
    proxy_key = os.path.join(tmpdir, "proxy.key")
    proxy_cert = os.path.join(tmpdir, "proxy.crt")
    proxy_proc = None
    try:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                        "-keyout", proxy_key, "-out", proxy_cert, "-days", "1", "-nodes",
                        "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                       check=True, capture_output=True, timeout=15)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            proxy_port = s.getsockname()[1]
        proxy_proc = subprocess.Popen([proxy_bin, "-key-file", private_key_file, "-cert", proxy_cert,
                                       "-tls-key", proxy_key, "-port", str(proxy_port), "-host", "localhost"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", proxy_port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            return {"ok": False, "error": "tesla-http-proxy did not start within 10 seconds"}
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(proxy_cert)
        req = urllib.request.Request(f"https://127.0.0.1:{proxy_port}/api/1/vehicles/fleet_telemetry_config",
                                     data=payload, method="POST",
                                     headers={"Authorization": "Bearer " + at, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                return {"ok": True, "vins": vins, "response": json.load(r), "new_refresh_token": new_rt}
        except urllib.error.HTTPError as e:
            body = e.read(2048).decode("utf-8", "replace")
            # new_rt surfaced even on failure: the token was already rotated by the refresh above, so
            # the caller must persist it or the old token is left stale.
            return {"ok": False, "error": f"fleet_telemetry_config failed HTTP {e.code}: {body[:500]}",
                    "new_refresh_token": new_rt}
        except Exception as e:
            return {"ok": False, "error": f"fleet_telemetry_config request failed: {e}",
                    "new_refresh_token": new_rt}
    finally:
        if proxy_proc:
            proxy_proc.terminate()
        shutil.rmtree(tmpdir, ignore_errors=True)
