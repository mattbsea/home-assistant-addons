"""Minimal ingress status page for the GitHub Runner add-on.

Polls each configured target's GitHub API runners endpoint on a timer and serves
a small self-contained HTML table showing each runner's online/busy state.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

POLL_SECONDS = 30
LISTEN_PORT = 8099


def api_url_for(target):
    if target["scope"] == "org":
        return f"https://api.github.com/orgs/{target['url']}/actions/runners"
    return f"https://api.github.com/repos/{target['url']}/actions/runners"


def fetch_runners(target, timeout=10):
    """Return the GitHub-reported runners list for this target, or None on failure."""
    req = urllib.request.Request(
        api_url_for(target),
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
    return body.get("runners", [])


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
    if runners is None:
        row["state"] = "unknown"
        row["detail"] = "GitHub API call failed — check the PAT and network"
        return row
    match = next((r for r in runners if r.get("name") == f"ha-{target['name']}"), None)
    if match is None:
        row["state"] = "not registered"
        row["detail"] = "No matching runner name reported by GitHub yet"
        return row
    if match.get("busy"):
        row["state"] = "busy"
    elif match.get("status") == "online":
        row["state"] = "online"
    else:
        row["state"] = "offline"
    labels = ", ".join(l["name"] for l in match.get("labels", []))
    row["detail"] = f"labels: {labels}"
    return row


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


class StatusCache:
    """Background poller shared by all request handlers."""

    def __init__(self, targets, poll_seconds=POLL_SECONDS):
        self._targets = targets
        self._poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._html = render_html([summarize_target(t, None) for t in targets])

    def poll_forever(self):
        while True:
            rows = [
                summarize_target(t, fetch_runners(t), fetch_latest_run(t))
                for t in self._targets
            ]
            with self._lock:
                self._html = render_html(rows)
            time.sleep(self._poll_seconds)

    def html(self):
        with self._lock:
            return self._html


def make_handler(cache):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = cache.html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass  # quiet; the add-on log already carries dockerd/runner output

    return Handler


def parse_targets_env(raw):
    """Parse the GH_STATUS_TARGETS env var. Treats unset or blank the same as an empty
    list — the shell side can produce an empty string for zero configured targets."""
    if not raw:
        return []
    return json.loads(raw)


def main():
    targets = parse_targets_env(os.environ.get("GH_STATUS_TARGETS"))
    cache = StatusCache(targets)
    threading.Thread(target=cache.poll_forever, daemon=True).start()
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), make_handler(cache))
    server.serve_forever()


if __name__ == "__main__":
    main()
