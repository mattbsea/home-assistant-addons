# Food Tracker (nutrition-mcp)

A self-hosted nutrition tracker in one add-on. It is a fork of
[akutishevsky/nutrition-mcp](https://github.com/akutishevsky/nutrition-mcp) that stores everything in
SQLite on this Home Assistant host instead of a cloud database. It gives you:

- **An MCP server for Claude**: log meals, water, weight and goals by chatting, and ask for summaries.
- **Apple Health sync**: two iOS Shortcuts push your daily totals into Apple Health and your active
  energy back to the server.
- **A dashboard**: today against your goals, 7/30-day trends, recent meals, water, weight, active
  calories and the time of the last Apple Health sync.

Supported architectures: `amd64` and `aarch64`. `armv7` is not supported (Bun publishes no build for it).

## Setup

1. Install and start the add-on. With no options set it generates an **MCP key** and a **Shortcut key** on
   first start and keeps them in `/data/keys.json` (see *Reading your keys*).
2. In the **Configuration** tab, set `dashboard_password` (a long one: the dashboard is on the internet).
3. Put an HTTPS reverse proxy in front of the add-on (next section).
4. Connect Claude (*Claude connector*) and, if you use Apple Health, the Shortcuts (*Apple Health
   Shortcuts*).

The add-on has no sidebar panel (no ingress): Claude and the iOS Shortcuts need real public URLs, and
the dashboard is served from your own domain.

## Options

All options are optional.

| Option | Purpose |
| --- | --- |
| `mcp_key` | The secret in the Claude connector URL: `https://YOUR-DOMAIN/mcp/YOUR_MCP_KEY`. At least 16 characters from `A-Z a-z 0-9 . _ ~ -` (it sits in a URL). Blank: one is generated once and stored. |
| `shortcut_key` | Bearer key the Apple Health Shortcuts send. At least 16 characters. Must differ from `mcp_key`, so a leaked Shortcut cannot open the MCP endpoint or the reverse. Blank: generated once and stored. |
| `dashboard_password` | The single password for the dashboard login. Use 12 or more characters. |
| `usda_api_key` | Optional [USDA FoodData Central](https://fdc.nal.usda.gov/api-key-signup) key for food lookups. |
| `timezone` | IANA name such as `Europe/Berlin`. Blank: Home Assistant's own timezone. |

A key in the Configuration tab always wins over the stored one and is never written to `keys.json`.
Changing a key invalidates the old one at once: update the Claude connector or the Shortcuts afterwards.

## Reverse proxy (NGINX Proxy Manager)

Create a **Proxy Host**:

| Field | Value |
| --- | --- |
| Domain Names | `your-domain.example.com` |
| Scheme | `http` |
| Forward Hostname / IP | the IP of your Home Assistant host |
| Forward Port | `18440` (or whatever you set on the add-on's **Network** tab) |
| Websockets Support | on |
| Block Common Exploits | on |

On the **SSL** tab choose a certificate, then turn on **Force SSL** and **HTTP/2 Support**.

The add-on trusts the proxy's `X-Forwarded-For` header (`TRUST_PROXY=1`) to rate-limit failed logins by
client address. Make sure only the proxy can reach port 18440: do not forward that port on your router.

## Claude connector

1. Read your MCP key (*Reading your keys*).
2. In Claude go to **Settings → Connectors → Add custom connector**.
3. URL: `https://YOUR-DOMAIN/mcp/YOUR_MCP_KEY`. Leave any OAuth fields empty.

The key in the URL is the only credential. Treat the URL like a password. Anything that does not match
gets the same generic 404.

## Apple Health Shortcuts

Two iOS Shortcuts ("Nutrition MCP Health" and "Nutrition MCP Activity") sync with Apple Health. Their
install and configuration steps, and the exact endpoints, are in the fork's
[`docs/apple-health-shortcuts.md`](https://github.com/mattbsea/nutrition-mcp/blob/ha-addon/docs/apple-health-shortcuts.md).
You need two values: `https://YOUR-DOMAIN` and your **Shortcut key**, sent as
`Authorization: Bearer YOUR_SHORTCUT_KEY`.

## Reading your keys

Generated keys are in `/data/keys.json` (mode 600). The add-on log says where the keys are but never
prints them. To show them, open a shell on the Home Assistant host (the *Advanced SSH & Web Terminal*
add-on with **Protection mode** off) and run:

```sh
docker ps --format '{{.Names}}' | grep food_tracker   # prints addon_<id>_food_tracker
docker exec addon_<id>_food_tracker show-keys
```

It prints `mcp_key` and `shortcut_key`. If you set a key in the Configuration tab, that key is the one
in force and `show-keys` reports it as not stored.

## Data and backups

Everything is under `/data`: `nutrition.db` (SQLite, with its `-wal` and `-shm` files), `keys.json` and
`exports/`. Supervisor includes `/data` in add-on backups.

The add-on is set to `backup: cold`: Supervisor stops it for the backup and starts it afterwards.
SQLite runs in WAL mode, and copying the database while it is being written can capture an inconsistent
copy. The app checkpoints and closes the database when it is stopped, so a cold backup is always a clean
single file. Expect a few seconds of downtime during each backup.

Restoring a backup restores your data **and** your keys, so the Claude connector and the Shortcuts keep
working. To move to a new host, restore the backup there.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Add-on stops at once with "mcp_key must be..." | The key is under 16 characters or has characters other than `A-Z a-z 0-9 . _ ~ -`. Fix or clear it. |
| Claude says the connector failed | Open `https://YOUR-DOMAIN/health`: it should show `ok`. If it does, the key in the URL is wrong (wrong keys get a 404). |
| `/health` fails through the proxy but not on `http://HOST:18440/health` | Proxy Host settings: scheme `http`, correct IP and port, certificate valid. |
| Dashboard login is rejected | `dashboard_password` is blank or was changed; restart the add-on after changing options. |
| Locked out after failed attempts | Failed attempts are rate-limited per client address; wait and retry. If every visitor looks like one address, the proxy is not sending `X-Forwarded-For`. |
| Dates are off by hours | Set `timezone` explicitly. |

## Security

- Port 18440 is published on the host. Keep it behind your HTTPS proxy and do not forward it from your router.
- The server logs the request path with the key redacted, and never logs key values.
- No third-party scripts: the dashboard serves everything from the add-on.
- The data is health data. Use a long `dashboard_password`, keep the connector URL private, and rotate a
  key (change it in the Configuration tab) if it leaks.

## Licence and credits

MIT. Based on [akutishevsky/nutrition-mcp](https://github.com/akutishevsky/nutrition-mcp) by Anton
Kutishevskyi; the upstream copyright notice is kept in the fork. The app is built from
[mattbsea/nutrition-mcp](https://github.com/mattbsea/nutrition-mcp) at the commit pinned in `build.yaml`.
