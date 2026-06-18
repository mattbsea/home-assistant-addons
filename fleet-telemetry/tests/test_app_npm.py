"""Phase 3 / 1b — single NPM client: cert fetch + content-based bundle extraction."""
import importlib
import io
import zipfile

npm = importlib.import_module("app.control.npm")

LEAF = b"-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n"
CHAIN = LEAF + b"-----BEGIN CERTIFICATE-----\nintermediate\n-----END CERTIFICATE-----\n"
KEY = b"-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n"


def _bundle(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_extract_cert_classifies_by_content():
    # filenames are deliberately unhelpful; classification is by content
    z = _bundle({"a.pem": KEY, "b.pem": LEAF, "c.pem": CHAIN})
    chain, key = npm.extract_cert(z)
    assert key == KEY
    assert chain == CHAIN          # the member with the most CERTIFICATE blocks


def test_extract_cert_missing_parts_raises():
    import pytest
    with pytest.raises(ValueError):
        npm.extract_cert(_bundle({"only.pem": LEAF}))  # no private key


def test_fetch_cert_writes_files(tmp_path):
    calls = {}

    def http_json(method, url, headers=None, data=None, timeout=30):
        if url.endswith("/api/tokens"):
            return 200, {"token": "TKN"}
        if url.endswith("/api/nginx/certificates"):
            return 200, [{"id": 7, "domain_names": ["doodle.mbarclay.org"]}]
        raise AssertionError(url)

    def get_bytes(url, tok, timeout=120):
        calls["download"] = url
        return _bundle({"k.pem": KEY, "fc.pem": CHAIN})

    r = npm.fetch_cert("https://npm:81", "e@x", "pw", "Doodle.MBarclay.org", str(tmp_path),
                       http_json=http_json, get_bytes=get_bytes)
    assert r["ok"] and r["certificate_id"] == 7
    assert "/api/nginx/certificates/7/download" in calls["download"]
    assert (tmp_path / "server.crt").read_bytes() == CHAIN
    assert (tmp_path / "server.key").read_bytes() == KEY
    assert oct((tmp_path / "server.key").stat().st_mode)[-3:] == "600"


def test_create_pubkey_host_reuse_and_create():
    def hj_reuse(method, url, headers=None, data=None, timeout=30):
        if url.endswith("/api/tokens"):
            return 200, {"token": "T"}
        if url.endswith("/api/nginx/proxy-hosts") and method == "GET":
            return 200, [{"id": 5, "domain_names": ["doodle.mbarclay.org"]}]
        raise AssertionError(url)
    r = npm.create_pubkey_host(base="https://npm", email="e", password="p", domain="Doodle.MBarclay.org",
                               forward_host="192.168.161.3", forward_port=8100, http_json=hj_reuse)
    assert r["ok"] and r.get("reused") and r["id"] == 5

    seq = []

    def hj_create(method, url, headers=None, data=None, timeout=30):
        if url.endswith("/api/tokens"):
            return 200, {"token": "T"}
        if url.endswith("/api/nginx/proxy-hosts") and method == "GET":
            return 200, []
        if url.endswith("/api/nginx/certificates") and method == "POST":
            return 201, {"id": 9}
        if url.endswith("/api/nginx/proxy-hosts") and method == "POST":
            seq.append(data)
            return 201, {"id": 12}
        raise AssertionError(url)
    r = npm.create_pubkey_host(base="https://npm", email="e", password="p", domain="d.org",
                               forward_host="h", forward_port=8100, http_json=hj_create)
    assert r["ok"] and r["id"] == 12 and r["certificate_id"] == 9
    assert seq[0]["forward_port"] == 8100 and seq[0]["certificate_id"] == 9 and seq[0]["ssl_forced"] is True


def test_create_stream_passthrough():
    def hj(method, url, headers=None, data=None, timeout=30):
        if url.endswith("/api/tokens"):
            return 200, {"token": "T"}
        if url.endswith("/api/nginx/streams") and method == "GET":
            return 200, []
        if url.endswith("/api/nginx/streams") and method == "POST":
            assert data["certificate_id"] == 0 and data["tcp_forwarding"] is True
            return 201, {"id": 3}
        raise AssertionError(url)
    r = npm.create_stream(base="https://npm", email="e", password="p", incoming_port=4443,
                          forward_host="h", forward_port=4543, http_json=hj)
    assert r["ok"] and r["id"] == 3 and r["incoming_port"] == 4443


def test_fetch_cert_no_match():
    def http_json(method, url, headers=None, data=None, timeout=30):
        if url.endswith("/api/tokens"):
            return 200, {"token": "T"}
        return 200, [{"id": 1, "domain_names": ["other.example"]}]
    r = npm.fetch_cert("https://npm", "e", "pw", "missing.example", "/tmp/x", http_json=http_json)
    assert r["ok"] is False and "no NPM certificate" in r["error"]
