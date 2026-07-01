# GitHub Runner Status Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the status page's unreadable plain table with a dark-themed card layout that also shows each target's latest workflow run and a link to its GitHub Actions tab.

**Architecture:** Same single-file Python stdlib server (`github-runner/status_server/server.py`), no new dependencies, no client-side JS. Two additive changes: (1) a new `fetch_latest_run` data-fetch function whose result flows through `summarize_target` as an extra field, (2) a rewritten `render_html` that consumes the enriched row dicts and emits card markup with an explicit dark palette instead of table markup with no color scheme.

**Tech Stack:** Python 3 stdlib only (`urllib.request`, `json`), `pytest` via `uv run pytest`.

## Global Constraints

- No client-side JS, no build step, no new dependencies — stays one Python stdlib file (per spec's "Out of scope").
- `render_html` must use `.get(...)` with fallbacks for any new row keys (`actions_url`, `latest_run`) so existing test rows built without those keys keep working — do not require callers to always set them.
- One hardcoded dark theme — no light/dark toggle (per spec's "Out of scope").
- Test with `cd github-runner && uv run pytest tests/test_server.py -v` — this is the exact command used throughout this add-on's test history.

---

### Task 1: `fetch_latest_run` + `summarize_target` enrichment

**Files:**
- Modify: `github-runner/status_server/server.py`
- Modify: `github-runner/tests/test_server.py`

**Interfaces:**
- Produces: `fetch_latest_run(target, timeout=10) -> dict | None` where the dict is
  `{"name": str, "status": str | None, "conclusion": str | None, "html_url": str}`.
  Returns `None` for org-scope targets (no single-repo run to show), on any fetch
  failure, or when the repo has no workflow runs yet.
- Produces: `actions_url_for(target) -> str` — `f"https://github.com/{target['url']}/actions"`
  for `scope == "repo"`, `f"https://github.com/{target['url']}"` (org homepage) for
  `scope == "org"`.
- Modifies: `summarize_target(target, runners, latest_run=None)` — new optional third
  parameter, default `None` so every existing call site keeps working unchanged. The
  returned row dict gains two new keys: `"actions_url"` (from `actions_url_for(target)`)
  and `"latest_run"` (the `latest_run` argument, passed through unchanged). Both keys
  are set once, before the existing early-return branches, so every existing branch
  keeps them without modification.

- [ ] **Step 1: Write the failing tests**

Open `github-runner/tests/test_server.py` and add these at the end of the file:

```python
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
```

Update the import line at the top of the file from:

```python
from server import api_url_for, summarize_target, render_html, parse_targets_env
```

to:

```python
from server import (
    api_url_for,
    summarize_target,
    render_html,
    parse_targets_env,
    actions_url_for,
    fetch_latest_run,
)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd github-runner && uv run pytest tests/test_server.py -v
```

Expected: `ImportError: cannot import name 'actions_url_for'` (neither `actions_url_for` nor `fetch_latest_run` exist yet).

- [ ] **Step 3: Implement `actions_url_for` and `fetch_latest_run`**

In `github-runner/status_server/server.py`, add these two functions immediately after
`fetch_runners` (which ends at the `return body.get("runners", [])` line):

```python
def actions_url_for(target):
    if target["scope"] == "org":
        return f"https://github.com/{target['url']}"
    return f"https://github.com/{target['url']}/actions"


def fetch_latest_run(target, timeout=10):
    """Return the target repo's most recent workflow run as
    {'name', 'status', 'conclusion', 'html_url'}, or None.

    None for org-scope targets (no single-repo run to show), on any fetch failure,
    or when the repo has no workflow runs yet.
    """
    if target["scope"] != "repo":
        return None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{target['url']}/actions/runs?per_page=1",
        headers={
            "Authorization": f"Bearer {target['token']}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None
    runs = body.get("workflow_runs", [])
    if not runs:
        return None
    run = runs[0]
    return {
        "name": run.get("name") or run.get("display_title") or "run",
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "html_url": run.get("html_url", "#"),
    }
```

- [ ] **Step 4: Update `summarize_target`'s signature and row construction**

Replace:

```python
def summarize_target(target, runners):
    """Reduce one target + its GitHub API runners list into a display row.

    `runners` is None when the API call failed, or a (possibly empty) list of
    GitHub's runner objects on success.
    """
    row = {"name": target["name"], "url": target["url"], "scope": target["scope"]}
```

with:

```python
def summarize_target(target, runners, latest_run=None):
    """Reduce one target + its GitHub API runners list into a display row.

    `runners` is None when the API call failed, or a (possibly empty) list of
    GitHub's runner objects on success. `latest_run` is whatever fetch_latest_run
    returned (a dict or None) — passed through unchanged for render_html to format.
    """
    row = {
        "name": target["name"],
        "url": target["url"],
        "scope": target["scope"],
        "actions_url": actions_url_for(target),
        "latest_run": latest_run,
    }
```

The rest of the function (the `if runners is None:` branch onward) is unchanged — it
already just adds keys onto the same `row` dict.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd github-runner && uv run pytest tests/test_server.py -v
```

Expected: all tests pass (13 total: 7 existing + 6 new).

- [ ] **Step 6: Commit**

```bash
git add github-runner/status_server/server.py github-runner/tests/test_server.py
git commit -m "github-runner: add fetch_latest_run and thread it through summarize_target"
```

---

### Task 2: Card-based dark-themed `render_html` + wiring

**Files:**
- Modify: `github-runner/status_server/server.py`
- Modify: `github-runner/tests/test_server.py`

**Interfaces:**
- Consumes: `summarize_target(target, runners, latest_run=None)` and its row dict shape
  from Task 1 (`name`, `url`, `scope`, `state`, `detail`, `actions_url`, `latest_run`).
- Modifies: `render_html(rows)` — same signature, entirely new HTML/CSS output. Must
  use `r.get("actions_url", "#")` and `r.get("latest_run")` (not `r["actions_url"]`)
  so the existing test that builds a raw row dict without those keys still passes.
- Modifies: `StatusCache.poll_forever` — now calls `fetch_latest_run(t)` per target
  and passes it as `summarize_target`'s third argument.

- [ ] **Step 1: Write the failing tests**

Add these to `github-runner/tests/test_server.py`, replacing the existing
`test_render_html_includes_target_name_and_state` test (keep the same row shape it
uses — this proves rows without `actions_url`/`latest_run` still render):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd github-runner && uv run pytest tests/test_server.py -v
```

Expected: the 4 new tests FAIL (`test_render_html_includes_actions_link` etc. — the
current table markup has no `href` attributes or "No runs yet"/"No targets configured"
text). `test_render_html_includes_target_name_and_state` still PASSES unchanged.

- [ ] **Step 3: Replace `render_html`**

Replace the entire existing `render_html` function (from `def render_html(rows):`
through the closing `"""` of its returned f-string) with:

```python
def render_html(rows):
    """Render the status rows as a small self-contained, dark-themed HTML page."""
    state_pill = {
        "online": ("#238636", "#ffffff"),
        "busy": ("#9e6a03", "#ffffff"),
        "offline": ("#da3633", "#ffffff"),
        "not registered": ("#30363d", "#c9d1d9"),
        "unknown": ("#30363d", "#c9d1d9"),
    }
    run_pill = {
        "success": ("#238636", "#ffffff"),
        "failure": ("#da3633", "#ffffff"),
        "in_progress": ("#9e6a03", "#ffffff"),
        "queued": ("#9e6a03", "#ffffff"),
        "cancelled": ("#30363d", "#c9d1d9"),
    }

    def run_html(latest_run):
        if latest_run is None:
            return '<div class="run muted">No runs yet</div>'
        conclusion = latest_run.get("conclusion") or latest_run.get("status") or "unknown"
        bg, fg = run_pill.get(conclusion, ("#30363d", "#c9d1d9"))
        name = latest_run.get("name", "run")
        url = latest_run.get("html_url", "#")
        return (
            f'<div class="run"><a href="{url}">{name}</a> '
            f'<span class="pill" style="background:{bg};color:{fg}">{conclusion}</span></div>'
        )

    def card_html(r):
        bg, fg = state_pill.get(r["state"], ("#30363d", "#c9d1d9"))
        actions_url = r.get("actions_url", "#")
        return f"""<div class="card">
  <div class="card-head">
    <span class="name">{r['name']}</span>
    <span class="pill" style="background:{bg};color:{fg}">{r['state']}</span>
  </div>
  {run_html(r.get('latest_run'))}
  <div class="meta">{r['scope']} &middot; {r['url']} &middot; <a href="{actions_url}">Actions</a></div>
  <div class="detail">{r['detail']}</div>
</div>"""

    cards = "\n".join(card_html(r) for r in rows) if rows else '<p class="muted">No targets configured.</p>'

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<title>GitHub Runner Status</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        margin: 0; padding: 1.5rem; background: #0d1117; color: #c9d1d9; }}
h1 {{ font-size: 1.4rem; margin: 0 0 1rem; color: #ffffff; }}
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
         padding: 1rem; margin-bottom: 1rem; }}
.card-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; }}
.name {{ font-weight: 600; font-size: 1.05rem; color: #ffffff; }}
.pill {{ display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px;
         font-size: 0.8rem; font-weight: 600; }}
.run {{ margin-bottom: 0.5rem; }}
.run a {{ color: #58a6ff; text-decoration: none; }}
.run a:hover {{ text-decoration: underline; }}
.meta {{ color: #8b949e; font-size: 0.85rem; margin-bottom: 0.25rem; }}
.meta a {{ color: #58a6ff; text-decoration: none; }}
.detail {{ color: #8b949e; font-size: 0.8rem; }}
.muted {{ color: #8b949e; }}
</style></head>
<body>
<h1>GitHub Runner Status</h1>
{cards}
</body></html>"""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd github-runner && uv run pytest tests/test_server.py -v
```

Expected: all tests pass (18 total: 13 from Task 1 + 5 new, with
`test_render_html_includes_target_name_and_state` updated in place).

- [ ] **Step 5: Wire `fetch_latest_run` into `StatusCache.poll_forever`**

Replace:

```python
    def poll_forever(self):
        while True:
            rows = [summarize_target(t, fetch_runners(t)) for t in self._targets]
            with self._lock:
                self._html = render_html(rows)
            time.sleep(self._poll_seconds)
```

with:

```python
    def poll_forever(self):
        while True:
            rows = [
                summarize_target(t, fetch_runners(t), fetch_latest_run(t))
                for t in self._targets
            ]
            with self._lock:
                self._html = render_html(rows)
            time.sleep(self._poll_seconds)
```

(`StatusCache.__init__`'s initial-state line, `render_html([summarize_target(t, None) for t in targets])`,
is unchanged — it intentionally does no network calls at construction, so `latest_run`
stays at its default of `None` until the first poll.)

- [ ] **Step 6: Run the full test suite one more time**

```bash
cd github-runner && uv run pytest tests/test_server.py -v
```

Expected: all 18 tests pass, output pristine (no warnings).

- [ ] **Step 7: Commit**

```bash
git add github-runner/status_server/server.py github-runner/tests/test_server.py
git commit -m "github-runner: redesign status page as dark-themed cards with latest-run info"
```

- [ ] **Step 8: Deploy and manually verify readability**

Bump `version` in `github-runner/config.yaml` from `0.2.1` to `0.2.2`.

Add this entry to the top of `github-runner/CHANGELOG.md`, above the existing `## 0.2.1 - 2026-07-01` line:

```markdown
## 0.2.2 - 2026-07-01

### Changed

- Redesigned the status page: card layout with an explicit dark theme (fixes text
  rendering unreadable — near-black on near-black — inside Home Assistant's dark
  mobile app, since the old table had no explicit colors), a status pill per target,
  a link to each repo's GitHub Actions tab, and the current/latest workflow run's
  name and conclusion. Auto-refreshes every 30s via a meta tag instead of requiring
  a manual reload.

```

Then commit and push:

```bash
git add github-runner/config.yaml github-runner/CHANGELOG.md
git commit -m "github-runner 0.2.2: card-based dark-themed status page"
git push origin main
```

Then (via the Portainer `dockerProxy` exec-into-SSH-addon technique used throughout
this session): `ha store reload`, then `ha_manage_addon(action="update", slug="a44b0313_github_runner")`,
then `ha_manage_addon(action="restart", slug="a44b0313_github_runner")`.

Finally fetch the live page and visually confirm cards render with readable light
text on dark backgrounds, status pills show the right colors, and the Actions links
are present:

```
ha_manage_addon(slug="a44b0313_github_runner", path="/")
```

Expected: `200` response, HTML containing `<div class="card">` entries for each
configured target (`car-lights`, `rv-touch-controller`, `rv-level-controller`), each
with a colored `<span class="pill">`, a `<div class="run">` (or "No runs yet"), and an
`<a href="...">Actions</a>` link.
