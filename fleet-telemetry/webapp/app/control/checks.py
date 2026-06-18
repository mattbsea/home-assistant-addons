"""Wizard verification checks (pubkey reachability, TLS cert detail), ported from the v0 server."""
import ipaddress
import re
import socket
import ssl
import subprocess
import time
import urllib.request


def check_pubkey(domain):
    domain = domain.strip().lower().rstrip(".")
    if not re.match(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)*$", domain):
        return {"ok": False, "error": "Invalid domain — must be a plain hostname like telemetry.example.org"}
    try:
        addrs = socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return {"ok": False, "error": "Domain does not resolve — check DNS for this domain."}
    resolved = []
    for _, _, _, _, addr in addrs:
        try:
            resolved.append(ipaddress.ip_address(addr[0]))
        except ValueError:
            pass
    for ip in resolved:
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            return {"ok": False, "error": "Domain resolves to a disallowed address (loopback/link-local/etc.). Enter your real public domain."}
    local_only = bool(resolved) and all(ip.is_private for ip in resolved)
    warning = ("Inside your network this domain resolves to a private/LAN IP (split-horizon DNS or "
               "NAT hairpin). The key is served correctly here — just make sure the domain is also "
               "reachable from the public internet so Tesla can fetch it.") if local_only else None
    url = f"https://{domain}/.well-known/appspecific/com.tesla.3p.public-key.pem"
    try:
        body = urllib.request.urlopen(url, context=ssl.create_default_context(), timeout=10).read(8192).decode("utf-8", "replace")
    except ssl.SSLError:
        return {"ok": False, "error": "TLS error fetching the public key — the domain's certificate is missing or not trusted yet. Confirm NPM issued a Let's Encrypt cert for this domain.", "warning": warning}
    except Exception:
        return {"ok": False, "error": "Could not reach the public-key URL. Check the domain is reachable and the NPM proxy host is running.", "warning": warning}
    if "BEGIN PUBLIC KEY" not in body and "BEGIN EC PUBLIC KEY" not in body:
        return {"ok": False, "error": "URL reachable but the content is not an EC public key PEM.", "warning": warning}
    return {"ok": True, "warning": warning}


def cert_detail(cert_file):
    try:
        out = subprocess.run(["openssl", "x509", "-noout", "-subject", "-enddate", "-in", cert_file],
                             capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            return {"ok": False, "error": out.stderr.strip() or "certificate file not found or invalid"}
        subject = not_after = ""
        for line in out.stdout.splitlines():
            if line.startswith("subject="):
                subject = line.split("=", 1)[1].strip()
            elif line.startswith("notAfter="):
                not_after = line.split("=", 1)[1].strip()
        days = None
        if not_after:
            try:
                days = round((time.mktime(time.strptime(not_after, "%b %d %H:%M:%S %Y %Z")) - time.time()) / 86400.0, 1)
            except ValueError:
                pass
        return {"ok": days is not None and days > 0, "subject": subject, "not_after": not_after, "days_left": days}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
