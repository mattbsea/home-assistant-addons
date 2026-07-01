import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "status_server"))

from server import api_url_for, summarize_target, render_html


def test_api_url_for_repo_scope():
    target = {"scope": "repo", "url": "mattbsea/home-assistant-addons"}
    assert api_url_for(target) == (
        "https://api.github.com/repos/mattbsea/home-assistant-addons/actions/runners"
    )


def test_api_url_for_org_scope():
    target = {"scope": "org", "url": "my-org"}
    assert api_url_for(target) == "https://api.github.com/orgs/my-org/actions/runners"


def test_summarize_target_unknown_on_fetch_failure():
    target = {"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo"}
    row = summarize_target(target, None)
    assert row["state"] == "unknown"


def test_summarize_target_not_registered_when_no_match():
    target = {"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo"}
    row = summarize_target(target, runners=[])
    assert row["state"] == "not registered"


def test_summarize_target_busy():
    target = {"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo"}
    runners = [{"name": "ha-addons", "status": "online", "busy": True, "labels": [{"name": "docker"}]}]
    row = summarize_target(target, runners)
    assert row["state"] == "busy"
    assert "docker" in row["detail"]


def test_summarize_target_online_idle():
    target = {"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo"}
    runners = [{"name": "ha-addons", "status": "online", "busy": False, "labels": []}]
    row = summarize_target(target, runners)
    assert row["state"] == "online"


def test_render_html_includes_target_name_and_state():
    rows = [{"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo",
             "state": "online", "detail": "labels: docker"}]
    html = render_html(rows)
    assert "addons" in html
    assert "online" in html
