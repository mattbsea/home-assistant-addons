# GitHub Runner Status Page Redesign — Design

## Problem

The current status page (`github-runner/status_server/server.py`) renders a plain HTML `<table>`
with no explicit text/background colors on `<td>` cells. Home Assistant's mobile app wraps the
ingress iframe in a dark theme; without explicit colors, cells inherit near-black-on-near-black
and the text is unreadable (confirmed via screenshot). The page is also minimal: it shows runner
online/busy/offline state but nothing about what's actually running.

## Design

**Layout:** one card per configured target, replacing the table. Each card:
- Target name (bold) + a colored status pill (`online` green, `busy` orange, `offline`/`not
  registered`/`unknown` red/gray — same semantics as today, just styled as a pill instead of
  plain colored text)
- A link to `https://github.com/<url>/actions` (the repo's Actions tab)
- Current/last workflow run: name + conclusion (`success`/`failure`/`in_progress`/etc.), fetched
  via one extra GitHub API call per target: `GET /repos/{owner}/{repo}/actions/runs?per_page=1`
- Secondary line: scope, repo/org url, and labels, in smaller muted text

**Theme:** explicit dark palette hardcoded in the page's own CSS (not relying on `prefers-color-
scheme` or inheriting from the wrapping iframe) — a dark slate background, light near-white body
text, and card backgrounds one shade lighter than the page background so cards read as distinct
surfaces. This guarantees readability regardless of what theme HA's app applies around the iframe.

**Refresh:** the page already auto-generates fresh HTML server-side every `POLL_SECONDS` (30s);
add a `<meta http-equiv="refresh" content="30">` so the browser reloads it automatically instead
of requiring a manual refresh — this doesn't change today's total data-freshness (still bounded by
the 30s poll), it just removes the manual-reload step.

## Data flow change

`fetch_runners` already hits `GET /repos/{owner}/{repo}/actions/runners`. Add a second fetch,
`fetch_latest_run(target)`, hitting `GET /repos/{owner}/{repo}/actions/runs?per_page=1` with the
same auth header, returning `{name, status, conclusion, html_url}` for the most recent run or
`None` on failure/no runs yet. `summarize_target` gains this as an optional second input and
includes it in the row dict; a failed/missing fetch renders as "no runs yet" rather than blocking
the runner-status part of the card.

## Testing

Same TDD approach as the existing test suite: `fetch_latest_run` and `render_html`'s new card
markup get unit tests with mocked/constructed inputs (no live network calls), matching the
existing `test_summarize_target_*` pattern. Manual verification: reload the real ingress page
after deploying and visually confirm readability + working links, since color contrast can't be
asserted by a unit test.

## Out of scope

- No client-side JS / no build step — stays a single Python stdlib file rendering a static HTML
  string, matching the existing architecture.
- No historical run list (just the single most-recent run) — YAGNI until asked for.
- No light-theme toggle — one hardcoded dark theme, matching the existing page's precedent (the
  add-on has no light/dark preference option today, and it doesn't need one for this fix).
