# GitHub Actions Runner Add-on Documentation

Runs one or more self-hosted [GitHub Actions](https://docs.github.com/en/actions/hosting-your-own-runners) runners with their own Docker-in-Docker engine, so container-image build workflows that run too long for GitHub-hosted runners can run on your own hardware instead.

## Prerequisite: USB disk mounted as a Supervisor Mount

Docker's image/layer cache and every job's checkout directory need real local disk, not the small HAOS system partition. Attach a USB disk to the Home Assistant host and add it under **Settings → System → Storage → Add Mount** with usage `media`, mount type `Local` (not a network share — Docker's storage driver requires a real local filesystem). Once added, it appears inside this add-on at `/media/<your-disk-name>`.

## Configuration Options

### `data_path`

Where Docker's data and every runner's registration/job-checkout state live. Defaults to `/media/usbdisk/github-runner` — change the `usbdisk` segment to match whatever name you gave the mount above.

### `targets`

A list of repos and/or orgs to run a runner for. Each entry:

| Field    | Description |
|----------|-------------|
| `name`   | A short identifier — becomes part of the runner's GitHub-visible name (`ha-<name>`) |
| `scope`  | `repo` or `org` |
| `url`    | `owner/repo` (repo scope) or `org-name` (org scope) |
| `token`  | A fine-grained Personal Access Token — see below |
| `labels` | Extra comma-separated runner labels beyond the defaults (`self-hosted,linux,x64,docker`) |

### Creating the PAT for a target

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token.
2. **Repo scope:** select the specific repository, then under Repository permissions grant **Administration: Read and write**.
3. **Org scope:** select the organization, then grant the **Self-hosted runners** organization permission (Read and write).
4. Paste the generated token into that target's `token` field.

A single PAT can't cover both a personal repo and an org at once — fine-grained tokens are scoped to one or the other. Give each target its own token.

## Architecture

One shared Docker daemon (rooted at `<data_path>/docker`) serves every configured target's runner process, so build caches are shared across targets. Runners are persistent — they stay registered and keep their `_work` directory across add-on restarts, so a repeat build is fast.

## Troubleshooting

- **"data_path is not writable" at startup** — the USB mount either isn't attached, isn't configured as a Supervisor Mount, or this add-on's `media` mapping isn't enabled. Check Settings → System → Storage.
- **A target never shows "Idle" on GitHub** — check the add-on log for `registration failed`; this almost always means the PAT lacks the right permission for that target's scope (see above).
- **Runner restarts constantly** — check the add-on log around the `restarting` line; the job it was mid-way through when killed will show as failed on GitHub's Actions tab for that repo.
