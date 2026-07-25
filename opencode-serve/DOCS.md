# OpenCode - Home Assistant Add-on

AI coding agent web interface powered by [OpenCode](https://opencode.ai).

## Installation

1. Add this repository to Home Assistant Supervisor
2. Navigate to **Settings → Add-ons → Add-on Store**
3. Find **OpenCode** and click **Install**

## Access

Click the **OpenCode** link in the sidebar to open the web interface.

## Configuration

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | Verbosity of add-on logs (`debug`, `info`, `warn`, `error`) |

## API Keys

Configure your AI provider API keys (Anthropic, OpenAI, etc.) through the OpenCode web interface after installation. Configuration is persisted in `/data`.

## How It Works

The add-on runs `opencode serve --hostname 127.0.0.1 --port 19876`, bound to loopback only. An nginx
reverse proxy listens on the ingress port (8099) and forwards to it. Home Assistant's ingress proxy
handles authentication and exposes the interface through the sidebar panel.

Persistent data is stored in the `/data` directory, which survives add-on restarts and updates.

### Ingress URL rewriting

OpenCode's web UI is a single-page app that doesn't know it's being served under a
Supervisor-assigned ingress path prefix (e.g. `/api/hassio_ingress/<token>/`) — it builds API
requests against the bare page origin. Two layers fix this:

1. **nginx `sub_filter`** rewrites `href=`/`src=` attributes, the Vite chunk-preload base path
   function, and worker asset paths in the server-rendered HTML/JS/CSS to include the ingress
   prefix, and injects `ingress-patch.js` into `<head>`.
2. **`ingress-patch.js`** (templated with the real ingress path at container start, same as
   `nginx.conf.template`) monkey-patches `window.fetch` and `window.EventSource` at runtime to
   prepend the ingress prefix to any `/api/...` or `/event...` request — including requests made as
   `fetch(new Request(url, init))`, which is how OpenCode's generated API client actually issues
   calls.

If ingress API calls ever break again, check the add-on's own nginx access log
(`ha_get_logs(source="supervisor", slug="<slug>", search="GET /api/")`) — if it's empty, the
browser is bypassing the add-on entirely and hitting Home Assistant Core's own `/api/` endpoint at
the bare origin instead.
