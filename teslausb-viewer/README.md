# TeslaUSB Viewer — Home Assistant Add-on

Browse and watch the Tesla dashcam & Sentry videos your car-lights Pi archives to this Home
Assistant host — from the Home Assistant sidebar, behind ingress.

## Features

- 📂 **Browse** Saved (and, on the roadmap, Sentry/Recent) events as a thumbnail grid, filter by date.
- 🎥 **Synchronized multi-camera playback** — up to six angles play together against a master clock, with play/pause/seek/speed.
- 📥 **Authenticated upload API** — the Pi archiver pushes clips directly to this host's own disk (no cloud backend, no rclone).
- 🔒 **Ingress for the UI, token-authenticated LAN API for uploads** — no anonymous write access.
- 📊 **Statistics entities** in Home Assistant via MQTT discovery (event counts, last event, disk usage, …).

## Quick start

1. Install the add-on and open its **Configuration** tab.
2. Set `teslacam_path` (default `/media/USBDisk/teslausb`) to where you want clips stored.
3. Start the add-on and open **TeslaUSB Viewer** from the sidebar.
4. Issue a Home Assistant long-lived access token and configure your Pi archiver to upload
   with it — see [`DOCS.md`](./DOCS.md)'s "Archiver setup" section.

See [`DOCS.md`](./DOCS.md) for full configuration, the HEVC codec note, and troubleshooting.
