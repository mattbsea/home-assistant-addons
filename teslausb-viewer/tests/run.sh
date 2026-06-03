#!/usr/bin/env bash
# Set up an isolated test environment (uv venv + fake rclone + sample TeslaCam tree) and
# run the API test suite. Requires `uv` on PATH.
set -euo pipefail

ADDON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

VENV="$WORK/venv"
DATA="$WORK/data"
BACKEND="$WORK/backend"
BIN="$WORK/bin"

# uv-managed virtual environment with the app deps + test client.
uv venv "$VENV" >/dev/null
VIRTUAL_ENV="$VENV" uv pip install --quiet -r "$ADDON_DIR/requirements.txt" httpx >/dev/null

# Fake rclone + ffmpeg first on PATH.
mkdir -p "$BIN"
cp "$ADDON_DIR/tests/fake_rclone.py" "$BIN/rclone"
cp "$ADDON_DIR/tests/fake_ffmpeg.py" "$BIN/ffmpeg"
chmod +x "$BIN/rclone" "$BIN/ffmpeg"

# Sample SavedClips event (front + back cameras, one minute).
EV="$BACKEND/teslacam/SavedClips/2024-01-15_10-30-22"
mkdir -p "$EV" "$DATA/cache"
printf '{"reason":"user_interaction_honk","city":"Seattle","est_lat":47.6,"est_lon":-122.3}' > "$EV/event.json"
printf 'PNGDATA' > "$EV/thumb.png"
head -c 4096 /dev/urandom > "$EV/2024-01-15_10-30-22-front.mp4"
head -c 4096 /dev/urandom > "$EV/2024-01-15_10-30-22-back.mp4"

# Sample RecentClips clips nested under a date sub-folder (non-flat layout) to prove the
# indexer descends into it and playback fetches by the clip's real path.
REC="$BACKEND/teslacam/RecentClips/2024-01-15"
mkdir -p "$REC"
head -c 2048 /dev/urandom > "$REC/2024-01-15_10-31-00-front.mp4"
head -c 2048 /dev/urandom > "$REC/2024-01-15_10-31-00-back.mp4"

printf '[test]\ntype = s3\n' > "$DATA/rclone.conf"

PATH="$BIN:$PATH" PYTHONPATH="$ADDON_DIR" \
  TUV_FAKE_BACKEND="$BACKEND" TUV_DATA_DIR="$DATA" \
  TUV_RCLONE_CONF="$DATA/rclone.conf" TUV_CACHE_DIR="$DATA/cache" \
  TUV_REMOTE_NAME=test TUV_REMOTE_PATH=teslacam TUV_MQTT_ENABLED=false \
  "$VENV/bin/python" "$ADDON_DIR/tests/test_api.py"
