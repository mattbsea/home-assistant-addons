# TeslaUSB Viewer — Home Assistant Add-on

Browse and watch the Tesla dashcam & Sentry videos that
[TeslaUSB](https://github.com/marcone/teslausb) archives to your cloud backend — from the
Home Assistant sidebar, behind ingress.

## Features

- 📂 **Browse** Saved (and, on the roadmap, Sentry/Recent) events as a thumbnail grid, filter by date.
- 🎥 **Synchronized multi-camera playback** — up to six angles play together against a master clock, with play/pause/seek/speed.
- ☁️ **Any TeslaUSB cloud backend** via rclone (S3/MinIO, Google Drive, Dropbox, OneDrive, B2, SMB/CIFS, SFTP, WebDAV).
- 🔒 **Ingress-only** — no exposed ports; a sidebar panel entry.
- 📊 **Statistics entities** in Home Assistant via MQTT discovery (event counts, last event, backend disk usage, …).

## Quick start

1. Install the add-on and open its **Configuration** tab.
2. Paste your existing `rclone.conf` (or the relevant `[remote]` block) into `rclone_conf`.
3. Set `remote_name` and `remote_path` (the folder holding `SavedClips/`).
4. Start the add-on and open **TeslaUSB Viewer** from the sidebar.

See [`DOCS.md`](./DOCS.md) for full configuration, the HEVC codec note, and troubleshooting.
