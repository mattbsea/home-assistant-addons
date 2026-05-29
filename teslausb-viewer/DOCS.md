# TeslaUSB Viewer

Browse and watch the Tesla dashcam / Sentry videos that
[TeslaUSB](https://github.com/marcone/teslausb) archives to your cloud backend — directly
inside Home Assistant, behind ingress, with statistics exported as sensors.

## How it works

TeslaUSB archives your TeslaCam footage to a backend (S3/MinIO, Google Drive, Dropbox,
OneDrive, Backblaze B2, SMB/CIFS, SFTP, WebDAV, …). All of those are
[rclone](https://rclone.org) remotes, so this add-on reads them through **one** layer:
rclone. You give it your existing rclone configuration; it indexes the events and streams
the video into your browser. When you open an event, its clips are copied to a local cache
on the Home Assistant host and served from there with seek support.

> **Ingress-only.** The viewer is reachable only through the Home Assistant sidebar panel.
> No port is exposed.

## Configuration

| Option | Description |
| --- | --- |
| `rclone_conf` | Paste your existing `rclone.conf` (or just the relevant `[remote]` block). This is the recommended way — it covers OAuth backends (Google Drive, Dropbox, OneDrive) with no re-authentication. |
| `remote_name` | The rclone remote to read (e.g. `minio`). Defaults to the first `[section]` in the config. |
| `remote_path` | Path **within** the remote where TeslaUSB writes, i.e. the folder that contains `SavedClips/`, `SentryClips/`, `RecentClips/` (e.g. `teslacam`). |
| `refresh_interval_minutes` | How often to re-scan the backend for new events (5–1440). |
| `cache_size_mb` | Maximum size of the local video cache before least-recently-watched events are evicted (256–51200). |
| `publish_mqtt` | Publish statistics to Home Assistant via MQTT discovery (needs the Mosquitto broker + MQTT integration). |
| `s3_endpoint` / `s3_access_key_id` / `s3_secret_access_key` / `s3_bucket` / `s3_region` | Optional guided fields for S3-compatible backends, used **only** when `rclone_conf` is empty. |

### Supplying credentials

> **Home Assistant has no multi-line / textarea option type** — every string option in the
> Configuration tab is a single-line input. So pick the method that fits your backend:

**Recommended for S3 / MinIO / B2 — the guided fields (all single-line):**

| Field | Example |
| --- | --- |
| `s3_endpoint` | `https://s3.example.org` |
| `s3_access_key_id` | `YOUR_ACCESS_KEY` |
| `s3_secret_access_key` | `YOUR_SECRET_KEY` (masked) |
| `s3_bucket` | `teslausb` (used as the path if `remote_path` is empty) |
| `remote_name` | `minio` (optional; defaults to `s3`) |

No `rclone.conf` needed — the add-on synthesises one at startup.

**For OAuth backends (Google Drive, Dropbox, OneDrive) or any existing remote — paste your
`rclone.conf`:** because it's multi-line, use the Configuration tab's **"Edit in YAML"
toggle** (top-right ⋮ menu) and paste under `rclone_conf: |`. For example:

```yaml
rclone_conf: |
  [gdrive]
  type = drive
  scope = drive
  token = {"access_token":"...","refresh_token":"...","expiry":"..."}
remote_name: gdrive
remote_path: TeslaCam
```

**Or drop a file** at `/data/rclone.conf` (e.g. via the Samba/SSH add-on) and leave the
options empty.

**Precedence:** pasted `rclone_conf` → guided S3 fields → existing `/data/rclone.conf`. The
effective config is written to `/data/rclone.conf` with `600` permissions on every start.

## Statistics entities

When an MQTT broker is available and `publish_mqtt` is on, a **TeslaUSB Viewer** device is
created with sensors: total events, Saved/Sentry/Recent counts, total video files, last
event (timestamp), Sentry events today, last index refresh (timestamp), and backend
used/free bytes. Backend bytes show as *unavailable* on remotes where rclone can't report
disk usage (common for plain S3).

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
  reports whether the backend was reachable at startup.
- **"Backend not reachable"** — verify `remote_name`/`remote_path` and that the pasted
  `rclone.conf` works (`rclone lsd remote:path` on any machine).
- **Black video** — almost always the HEVC codec issue above; try Safari.
