# OmniRoute — Home Assistant Add-on

AI gateway that routes requests across multiple LLM providers through a single OpenAI-compatible endpoint.

## Features

- **Smart routing** — Auto-Combo picks the best available model
- **270+ providers** — Anthropic, OpenAI, Google, Ollama, and more
- **Load balancing & fallbacks** — Automatic failover between providers
- **Free tier aggregation** — 1.4B+ free tokens/month from documented free tiers
- **Compression** — RTK + Caveman stacked compression saves 15–95% tokens
- **Dashboard** — Full web UI for managing providers, combos, analytics

## Configuration

### Option: `omniroute_password`

Password to protect the OmniRoute dashboard and API.

```yaml
omniroute_password: "my-secure-password"
```

### Option: `setup_providers`

List of provider names to auto-configure during first setup. Leave empty to configure manually via the dashboard.

```yaml
setup_providers:
  - openai
  - anthropic
```

## Dashboard Access

Once the add-on is running, access the dashboard via:

- **Home Assistant sidebar** — Click the OmniRoute panel icon
- **Direct URL** — `http://<home-assistant-ip>:20128/dashboard`

## API Access

The OpenAI-compatible API endpoint is available at:

```
http://<home-assistant-ip>:20128/v1
```

### Connecting OpenCode

Add to `~/.config/opencode/opencode.json`:

```jsonc
{
  "provider": {
    "omniroute": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "OmniRoute",
      "options": {
        "baseURL": "http://<home-assistant-ip>:20128/v1",
        "apiKey": "<your-dashboard-key>"
      },
      "models": {
        "auto": { "name": "Auto-Combo" }
      }
    }
  }
}
```

### Connecting Claude Code

```bash
# Set the base URL and API key in Claude Code config
# or use the OmniRoute setup command from the terminal
```

## Local Models (Ollama)

To use local models, install Ollama separately and add it as a provider in the OmniRoute dashboard:

1. Install the Ollama add-on or run Ollama on another machine
2. In OmniRoute dashboard, go to **Providers** → **Add Provider**
3. Select **OpenAI Compatible** and enter the Ollama URL (e.g., `http://<ollama-host>:11434/v1`)
4. Models will appear in the dashboard and become routable

## Troubleshooting

### Dashboard not loading

Check the add-on logs for startup errors. Ensure port 20128 is not already in use.

### API returns 401

Create an API key in the OmniRoute dashboard under **Endpoints**.

### Provider connection failed

Run `omniroute doctor` from the add-on terminal (if available) or check the dashboard **Health** tab.
