# Paseo

[Paseo](https://paseo.sh) is a self-hosted control plane for coding agents. This add-on runs the
official Paseo daemon (image `ghcr.io/getpaseo/paseo`) with its bundled web UI and adds the
**Claude Code**, **Codex** and **OpenCode** CLIs, so you can start and supervise agents from:

- the **Paseo** entry in the Home Assistant sidebar,
- any browser at your public HTTPS address (e.g. `https://paseo.example.com`),
- the **Paseo iPhone / Android app**.

## How it fits together

```
iPhone app / browser ──HTTPS──> reverse proxy (NPM) ──HTTP──> add-on :6767 (daemon + web UI + /ws)
HA sidebar ──ingress──> add-on :8099 (wrapper page) ──iframe──> https://paseo.example.com
```

The Paseo web app uses absolute paths and can't be served under Home Assistant's ingress
prefix, so the sidebar panel is a small page that frames `external_url`. The web UI therefore
needs to be reachable over HTTPS through a reverse proxy (below).

## Password

Leave `password` empty on install. On first start the add-on generates a random password and
saves it back to this add-on's **Configuration** tab, where you can view or change it. Every
client (web UI, sidebar, phone app) asks for it once. To rotate it, enter a new value (or clear
the field to generate a fresh one) and restart the add-on.

The password is only ever kept in Home Assistant's add-on options (and hashed by Paseo); it is
never written to the add-on's git repository.

## Reverse proxy (Nginx Proxy Manager)

Create a **Proxy Host**:

- Domain: `paseo.example.com`
- Forward: `http` → your Home Assistant host IP, port `6767`
- **Websockets Support**: on
- SSL: a Let's Encrypt certificate, **Force SSL** on
- Advanced → Custom Nginx configuration:

  ```nginx
  proxy_buffering off;
  proxy_read_timeout 3600s;
  proxy_send_timeout 3600s;
  client_max_body_size 100m;
  ```

Then set the add-on options `external_url: https://paseo.example.com` and
`hostnames: [paseo.example.com]`, and restart the add-on. Check it with
`curl https://paseo.example.com/api/health` (this endpoint needs no password).

## iPhone app

1. Install **Paseo** from the App Store.
2. **Settings → Add host → Direct connection**.
3. Host `paseo.example.com`, port `443`, **Use SSL** on.
4. Enter the password from the add-on's Configuration tab.

The Paseo relay is disabled; the app connects straight to your HTTPS address.

## Logging in to the agents

Paseo runs the agent CLIs as the `paseo` user with its own home directory in the add-on's
persistent storage, so each agent needs to be logged in once:

- **Claude Code** — open a terminal in Paseo and run `claude`, then `/login`. Alternatively run
  `claude setup-token` anywhere and paste the token into `claude_code_oauth_token`, or set
  `anthropic_api_key`.
- **Codex** — in a Paseo terminal run `codex login --device-auth`, or set `openai_api_key` (the
  add-on runs `codex login --with-api-key` for you on start).
- **OpenCode** — in a Paseo terminal run `opencode auth login` and pick a provider.

Logins survive restarts and add-on updates.

## Options

| Option | Default | Description |
| --- | --- | --- |
| `password` | *(generated)* | Password all clients must use. Empty = generate and save one. |
| `external_url` | `https://paseo.mbarclay.org` | HTTPS address of the daemon; the sidebar panel frames it. |
| `hostnames` | `[paseo.mbarclay.org]` | Extra `Host` headers the daemon accepts (DNS-rebinding guard). IPs and `localhost` are always allowed. |
| `trusted_proxies` | `loopback, 172.30.32.0/23` | Proxies whose `X-Forwarded-*` headers are trusted (the HA add-on network, where NPM's traffic arrives from). |
| `workspace_dir` | `/share/paseo` | Directory the daemon starts in; put repositories here (also visible to other add-ons via `/share`). |
| `claude_code_oauth_token` | | Optional `CLAUDE_CODE_OAUTH_TOKEN` for Claude Code. |
| `anthropic_api_key` | | Optional `ANTHROPIC_API_KEY`. |
| `openai_api_key` | | Optional OpenAI key for Codex. |

## Storage

- `/data/home` — Paseo state (`.paseo`), agent logins and config (`.claude`, `.codex`,
  `.config/opencode`) and caches. Included in add-on backups; treat backups as sensitive.
- `/share/paseo` — default workspace for your repositories.

## Security notes

- Port 6767 is published on your LAN and is protected by the password; password auth protects
  access but does not encrypt traffic, so use the HTTPS address from outside your network.
- The static web UI files load before login by design; all API and WebSocket traffic requires
  the password.
- Agents can run arbitrary commands inside this add-on's container with access to `/share`.
  They inherit the daemon's environment, which includes the Paseo password and any agent API
  keys you set (Paseo reads its password only from the environment); the Supervisor API token is
  removed before the daemon starts.
- Microphone/clipboard features may be blocked inside the sidebar frame; use the panel's
  **Open in new tab** link (or the phone app) when you need them.
