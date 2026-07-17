# TeslaUSB Viewer

Browse and watch the Tesla dashcam / Sentry videos your car-lights Pi archives to this Home
Assistant host — directly inside Home Assistant, behind ingress, with statistics exported as
sensors.

## How it works

The Pi-based archiver (a ROCK Pi 4C+ running the car-lights TeslaUSB gadget) pushes clips
straight to this add-on over the LAN, one file at a time, via an authenticated upload API —
no cloud backend, no rclone. Uploaded files land on this Home Assistant host's own disk (at
`teslacam_path`, e.g. a mounted USB drive) in the same `SavedClips/SentryClips/RecentClips`
layout Tesla itself uses. This add-on just indexes and serves what's on disk.

> **Two ports, two trust levels — read this.** The browse/watch UI is reachable ONLY through
> the Home Assistant sidebar panel (ingress, port 8099) — that port has no LAN or external
> exposure at all, restoring the original ingress-only access model. A second, dedicated port
> (**8101**) serves *only* the upload endpoint (`PUT /api/upload/...`) — nothing else is
> reachable through it, enforced by which port the connection arrived on, not by a header a
> client could fake. Because port 8101 can never serve anything but the already
> token-authenticated upload route, it's safe to expose it on your LAN, or even externally
> through your own reverse proxy (e.g. NGINX Proxy Manager) — see "Archiver setup" below.

## Configuration

| Option | Description |
| --- | --- |
| `teslacam_path` | Path **inside the container** where TeslaCam clips live (and where the upload API writes them) — the folder that contains (or will contain) `SavedClips/`, `SentryClips/`, `RecentClips/`. Defaults to `/media/USBDisk/teslausb`; requires `/media` to be reachable on the host (this add-on's `map` includes `media:rw`). |
| `refresh_interval_minutes` | How often to re-scan `teslacam_path` for new events (5–1440). Also bounds how long an orphaned upload temp file can linger before being swept. |
| `cache_size_mb` | Maximum size of the on-disk thumbnail cache before older entries are reclaimed (256–51200). |
| `publish_mqtt` | Publish statistics to Home Assistant via MQTT discovery (needs the Mosquitto broker + MQTT integration). |

## Archiver setup (authenticating the Pi)

The upload API validates the caller against Home Assistant itself — issue the Pi a **long-
lived access token**:

1. In Home Assistant, open your user profile → **Security** → **Long-lived access tokens** →
   **Create token**. Copy it immediately (shown once).
2. Configure the Pi archiver with that token and this add-on's LAN address, e.g.
   `http://<home-assistant-host>:8101/api/upload/`.
3. The archiver `PUT`s each clip file to
   `.../api/upload/<SavedClips|SentryClips|RecentClips>/<event_dir>/<filename>` with
   `Authorization: Bearer <token>` and the raw file bytes as the body.
4. **Upload order matters.** For each event, upload all clip `.mp4` files first, then
   `event.json` (if applicable), and upload `thumb.png` **last**. The indexer treats the
   presence of `thumb.png` as its sole signal that an event is fully uploaded and complete —
   once a scan observes it, that event is never re-checked again. If `thumb.png` arrives
   before the clips and a scan happens to run in between, the later clips can be silently and
   permanently missing from the index until a full re-index.

## Statistics entities

When an MQTT broker is available and `publish_mqtt` is on, a **TeslaUSB Viewer** device is
created with sensors: total events, Saved/Sentry/Recent counts, total video files, last
event (timestamp), Sentry events today, last index refresh (timestamp), and disk used/free
bytes for `teslacam_path`.

## Codec note (HEVC)

Tesla HW3+ vehicles record **H.265/HEVC**. Safari plays this natively; Chrome/Firefox play
it only when the operating system provides HEVC decoding, otherwise the affected camera
tile shows a "can't decode" message. Browsing, thumbnails and Saved/Sentry indexing work
everywhere — only playback depends on the codec. On-the-fly transcoding to H.264 is a
planned enhancement.

## Current scope

This first release indexes **SavedClips**, browses them, and plays synchronized
multi-camera footage one minute ("scene") at a time. SentryClips/RecentClips, transcoding,
and a guided first-run setup screen are on the roadmap (see `CHANGELOG.md`).

## Troubleshooting

- **No events / empty list** — click **↻ Refresh**, and check the add-on log. The log
  reports whether `teslacam_path` was present at startup.
- **"teslacam_path not available"** — verify the option points at a path under `/media`
  (this add-on only has access to `/media`, via its `map: media:rw`), and that the directory
  exists (the add-on creates it at startup if missing, but a typo'd path under a drive that
  isn't mounted will not appear).
- **Uploads return 401** — the long-lived access token is missing, expired, or was revoked;
  issue a new one (see "Archiver setup") and update the Pi's configuration.
- **Black video** — almost always the HEVC codec issue above; try Safari.
