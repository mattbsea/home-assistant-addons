# AGENTS.md

## Repo overview

Multi-add-on Home Assistant Supervisor repository. Each add-on lives in its own directory with `config.yaml`, `Dockerfile`, `build.yaml`, `run.sh`, `DOCS.md`, `CHANGELOG.md`.

**Add-ons:** `claude-terminal/` (flagship, Node.js + shell), `claude-terminal-dev/` (dev build), `fleet-telemetry/` (Python/FastAPI), `teslausb-viewer/` (Python/FastAPI), `omni-route/` (Node.js/Next.js), `rustdesk-server/`, `rustdesk-web/`, `pai/`, `opencode-serve/`, `portainer-mcp/`, `nginx-proxy-manager-mcp/`, `github-runner/`

## Dev environment

```bash
nix develop   # or: direnv allow
```

Provides: podman, hadolint, curl, jq, yq-go. Aliases: `build-addon`, `run-addon`, `lint-dockerfile`, `test-endpoint`.

Build (manual): `podman build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm -t local/<name> ./<name>`

## Running tests

Each add-on has its own test setup. There is no repo-wide test runner.

| Add-on | Command | Framework |
|--------|---------|-----------|
| `claude-terminal` | `cd claude-terminal/web-terminal && npm test` | Node.js built-in `node:test` |
| `fleet-telemetry` | `cd fleet-telemetry && uv run pytest` | pytest (uv venv, `tests/conftest.py` adds `webapp/` to `sys.path`) |
| `teslausb-viewer` | `cd teslausb-viewer && bash tests/run.sh` | Custom runner: creates uv venv + fake ffmpeg + sample tree, runs pytest modules sequentially |

**No lint/typecheck tooling is configured** in this repo (no eslint, ruff, mypy, tsc). The only lint tool is `hadolint` for Dockerfiles.

## Add-on file conventions

- **Shebang:** `#!/usr/bin/with-contenv bashio` for all `run.sh` and helper scripts
- **Dockerfiles:** Always `ARG BUILD_FROM` + `FROM ${BUILD_FROM}` — never hardcode `FROM`
- **build.yaml:** Maps arch to base image (e.g. `ghcr.io/home-assistant/amd64-base-debian:bookworm`)
- **Logging:** `bashio::log.info`, `bashio::log.warning`, `bashio::log.error`
- **Config reading:** `bashio::config 'option_name'` in shell
- **Final process:** Use `exec` for the terminal process in `run.sh` (proper signal handling)
- **Indentation:** 2 spaces YAML, 4 spaces shell scripts

## Version bumps

Bump `version:` in `config.yaml` on every change. Home Assistant Supervisor caches versions — without a bump, users won't see the update. Commit the version bump with the change.

## Multi-architecture

`build.yaml` defines per-arch base images. `config.yaml` lists supported `arch:`. Some add-ons are single-arch (e.g. `fleet-telemetry` is amd64-only because the upstream binary is amd64-only). `bun` is skipped on armv7 in `claude-terminal/Dockerfile`.

## Key architectural notes

- **claude-terminal:** Express + xterm.js + node-pty web terminal (`web-terminal/server.js`). Tabs configured via `CLAUDE_TAB_CONFIG` env var (JSON). Uses nvm's Node 24 (installed at build time). `/home/claude` is symlinked to `/data/home` at runtime for persistence.
- **fleet-telemetry:** Multi-stage Dockerfile (pulls upstream Tesla binary + builds `tesla-http-proxy` from Go source). Python webapp under `webapp/`. Configured via built-in setup wizard, not config.yaml options.
- **teslausb-viewer:** FastAPI app under `app/`. Uses `uv` for Python dependency management. Ingress on 8099, upload API on 8101 (port-restricted).
- **omni-route:** No ingress (Next.js routing breaks under HA's prefix-stripping proxy). Accessed via exposed port directly.

## GitHub workflows

- `claude.yml` — Claude Code Action triggered by `@claude` mentions in issues/PRs
- `claude-code-review.yml` — Auto-reviews PRs using Claude Code

No build/test CI workflows exist. Builds happen via Home Assistant's builder system when users check for updates.

## Gotchas

- `bashio::config` returns the string `"null"` for unset values — always check for this (see `run.sh:329-332`)
- Em-dashes (`—`) from mobile keyboards are normalized to `--` in claude_args (see `run.sh:328`)
- `pip3 install` requires `--break-system-packages` on Debian bookworm
- Claude/OpenCode CLI binaries are backed up at `/opt/claude-cli` and `/opt/opencode-cli` during build to survive the `/home/claude` symlink replacement at runtime
- `fleet-telemetry` tests neutralize env vars BEFORE importing webapp modules to avoid touching real `/data` files (see `conftest.py`)
- `teslausb-viewer` tests need `uv` on PATH and create a full fake TeslaCam directory tree
