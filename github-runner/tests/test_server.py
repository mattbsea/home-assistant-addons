import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "status_server"))

from server import (
    api_url_for,
    summarize_target,
    render_html,
    parse_targets_env,
    actions_url_for,
    fetch_latest_run,
)


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


def test_render_html_includes_actions_link():
    rows = [{"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo",
             "state": "online", "detail": "labels: docker",
             "actions_url": "https://github.com/mattbsea/home-assistant-addons/actions"}]
    html = render_html(rows)
    assert 'href="https://github.com/mattbsea/home-assistant-addons/actions"' in html


def test_render_html_links_open_in_new_tab():
    rows = [{"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo",
             "state": "online", "detail": "labels: docker",
             "actions_url": "https://github.com/mattbsea/home-assistant-addons/actions",
             "latest_run": {"name": "build-test", "status": "completed",
                             "conclusion": "success", "html_url": "https://x/runs/1"}}]
    html = render_html(rows)
    # both the Actions link and the latest-run link must open in a new tab, not the ingress iframe
    assert html.count('target="_blank" rel="noopener noreferrer"') == 2


def test_render_html_shows_no_runs_yet_when_latest_run_none():
    rows = [{"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo",
             "state": "online", "detail": "labels: docker", "latest_run": None}]
    html = render_html(rows)
    assert "No runs yet" in html


def test_render_html_includes_latest_run_name_and_conclusion():
    rows = [{"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo",
             "state": "online", "detail": "labels: docker",
             "latest_run": {"name": "build-test", "status": "completed",
                             "conclusion": "success", "html_url": "https://x/runs/1"}}]
    html = render_html(rows)
    assert "build-test" in html
    assert "success" in html
    assert 'href="https://x/runs/1"' in html


def test_render_html_no_targets_message():
    html = render_html([])
    assert "No targets configured" in html


def test_render_html_escapes_target_fields():
    rows = [{"name": "<script>alert(1)</script>", "url": "mattbsea/x", "scope": "repo",
             "state": "online", "detail": "<img src=x onerror=alert(1)>"}]
    html = render_html(rows)
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html


def test_render_html_escapes_latest_run_fields():
    rows = [{"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo",
             "state": "online", "detail": "labels: docker",
             "latest_run": {"name": "<script>alert(1)</script>", "status": "completed",
                             "conclusion": "success", "html_url": "https://x/runs/1"}}]
    html = render_html(rows)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_html_rejects_non_http_href():
    rows = [{"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo",
             "state": "online", "detail": "labels: docker",
             "actions_url": "javascript:alert(1)"}]
    html = render_html(rows)
    assert "javascript:" not in html
    assert 'href="#"' in html


def test_parse_targets_env_none_is_empty_list():
    assert parse_targets_env(None) == []


def test_parse_targets_env_blank_string_is_empty_list():
    assert parse_targets_env("") == []


def test_parse_targets_env_parses_json_array():
    raw = '[{"name": "addons", "scope": "repo", "url": "mattbsea/home-assistant-addons"}]'
    result = parse_targets_env(raw)
    assert result == [{"name": "addons", "scope": "repo", "url": "mattbsea/home-assistant-addons"}]


def test_actions_url_for_repo_scope():
    target = {"scope": "repo", "url": "mattbsea/car-lights"}
    assert actions_url_for(target) == "https://github.com/mattbsea/car-lights/actions"


def test_actions_url_for_org_scope():
    target = {"scope": "org", "url": "my-org"}
    assert actions_url_for(target) == "https://github.com/my-org"


def test_fetch_latest_run_returns_none_for_org_scope():
    target = {"scope": "org", "url": "my-org", "token": "x"}
    assert fetch_latest_run(target) is None


def test_summarize_target_includes_actions_url():
    target = {"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo"}
    row = summarize_target(target, runners=[])
    assert row["actions_url"] == "https://github.com/mattbsea/home-assistant-addons/actions"


def test_summarize_target_passes_through_latest_run():
    target = {"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo"}
    latest_run = {"name": "build-test", "status": "completed", "conclusion": "success", "html_url": "https://x"}
    row = summarize_target(target, runners=[], latest_run=latest_run)
    assert row["latest_run"] == latest_run


def test_summarize_target_latest_run_defaults_to_none():
    target = {"name": "addons", "url": "mattbsea/home-assistant-addons", "scope": "repo"}
    row = summarize_target(target, runners=[])
    assert row["latest_run"] is None
