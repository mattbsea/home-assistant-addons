"""Phase 3 — Tesla OAuth + partner registration (control plane)."""
import importlib

tesla = importlib.import_module("app.control.tesla")


def test_fleet_host_and_authorize_url():
    assert tesla.fleet_host("eu").endswith("eu.vn.cloud.tesla.com")
    assert tesla.fleet_host(None) == tesla.FLEET_HOSTS["na"]
    url = tesla.authorize_url("CID", "https://cb", "st8", auth_host="https://auth")
    assert url.startswith("https://auth/oauth2/v3/authorize?")
    assert "client_id=CID" in url and "state=st8" in url and "response_type=code" in url


def test_exchange_code_success_and_validation():
    def form_post(url, fields):
        assert fields["grant_type"] == "authorization_code"
        return 200, {"refresh_token": "RT", "access_token": "AT"}
    r = tesla.exchange_code(client_id="c", client_secret="s", code="abc", redirect_uri="https://cb",
                            region="na", form_post=form_post)
    assert r == {"ok": True, "refresh_token": "RT"}

    assert tesla.exchange_code(client_id="", client_secret="s", code="x", redirect_uri="r",
                               region="na", form_post=form_post)["ok"] is False
    bad = tesla.exchange_code(client_id="c", client_secret="s", code="x", redirect_uri="r",
                              region="na", form_post=lambda u, f: (400, {"error": "bad"}))
    assert bad["ok"] is False


def test_register_partner_flow():
    def form_post(url, fields):
        assert fields["grant_type"] == "client_credentials"
        return 200, {"access_token": "AT"}

    seen = {}

    def http_json(method, url, headers=None, data=None, timeout=30):
        seen["url"] = url
        seen["domain"] = data["domain"]
        return 200, {"response": {"domain": data["domain"]}}

    r = tesla.register_partner(client_id="c", client_secret="s", domain="Doodle.MBarclay.org",
                               region="na", form_post=form_post, http_json=http_json)
    assert r["ok"] is True
    assert seen["url"].endswith("/api/1/partner_accounts")
    assert seen["domain"] == "doodle.mbarclay.org"            # lowercased

    # token failure surfaces
    assert tesla.register_partner(client_id="c", client_secret="s", domain="d.org", region="na",
                                  form_post=lambda u, f: (401, {}), http_json=http_json)["ok"] is False
