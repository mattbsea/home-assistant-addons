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


def summarize_target(target, runners):
    """Reduce one target + its GitHub API runners list into a display row.

    `runners` is None when the API call failed, or a (possibly empty) list of
    GitHub's runner objects on success.
    """
    row = {"name": target["name"], "url": target["url"], "scope": target["scope"]}
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
    """Render the status rows as a small standalone HTML page (no external assets)."""
    state_colors = {
        "online": "#2e7d32",
        "busy": "#ef6c00",
        "offline": "#c62828",
        "not registered": "#757575",
        "unknown": "#757575",
    }
    body_rows = "\n".join(
        f"<tr><td>{r['name']}</td><td>{r['scope']}</td><td>{r['url']}</td>"
        f"<td style='color:{state_colors.get(r['state'], '#000')}'>{r['state']}</td>"
        f"<td>{r['detail']}</td></tr>"
        for r in rows
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>GitHub Runner Status</title>
<style>
body {{ font-family: sans-serif; margin: 1.5rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; }}
th {{ background: #f5f5f5; }}
</style></head>
<body>
<h1>GitHub Runner Status</h1>
<table>
<tr><th>Target</th><th>Scope</th><th>Repo/Org</th><th>State</th><th>Detail</th></tr>
{body_rows}
</table>
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
            rows = [summarize_target(t, fetch_runners(t)) for t in self._targets]
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


def main():
    targets = json.loads(os.environ.get("GH_STATUS_TARGETS", "[]"))
    cache = StatusCache(targets)
    threading.Thread(target=cache.poll_forever, daemon=True).start()
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), make_handler(cache))
    server.serve_forever()


if __name__ == "__main__":
    main()
