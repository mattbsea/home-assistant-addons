# Paseo Hub

[Paseo Hub](https://paseo.sh/docs/hub) is the self-hosted automation layer for Paseo: triggers from
GitHub, Slack and Discord start agents on your Paseo daemons, and Hub records what arrived, what it
matched and what ran. This add-on runs `@getpaseo/hub` (pinned in `build.yaml`).

## Setup

1. Put an HTTPS reverse proxy (e.g. Nginx Proxy Manager) in front of port **3000**, for example
   `hub.example.com`. Hub's web app uses absolute paths, so there is no sidebar/ingress panel.
2. Set `app_url` to that public URL. GitHub and Slack webhooks are sent to it.
3. Recommended: set `bootstrap_owner_email` and `bootstrap_owner_password` (12-128 characters) to create
   the first account on start, and replace the temporary password after signing in. Without them the
   first visitor to `app_url` can claim the owner account, so open it yourself before exposing the proxy.
4. Start the add-on and open `app_url`.
5. Connect the Paseo daemon (the Paseo add-on, or any machine):

   ```sh
   paseo hub connect https://hub.example.com
   ```

## Options

| Option | Purpose |
| --- | --- |
| `app_url` | Public URL of Hub (`PASEO_HUB_APP_URL`). |
| `trusted_client_ip_header` | Header your proxy sets with the client IP; `x-real-ip` for NPM (Hub only accepts a header holding a single IP, so not `x-forwarded-for`). Empty to disable. |
| `bootstrap_organization`, `bootstrap_owner_email`, `bootstrap_owner_password` | Unattended first account. |
| `database_url` | PostgreSQL URL. Empty uses the embedded database (one Hub process). |
| `env_vars` | Extra variables, e.g. `GITHUB_APP_ID`, `GITHUB_WEBHOOK_SECRET`, `SLACK_*`, `DISCORD_*`. See the [self-hosting guide](https://paseo.sh/docs/hub/self-hosting). |

## Data and upgrades

The embedded database and Hub's generated auth secret live in `/data/paseo-hub` and are included in
add-on backups (with `database_url` set, both live in PostgreSQL instead). Migrations run on start; take a backup before upgrading.

## Security

Port 3000 is published on the host; restrict it so only your reverse proxy can reach it, since clients
that connect directly can set the client-IP header themselves.

Anyone who can push to the repository holding your `.paseo` bundle controls what agents run. Use
`from_users` allowlists on external triggers and read the [Hub security guide](https://paseo.sh/docs/hub/security).
