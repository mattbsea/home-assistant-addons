#!/usr/bin/env bash
# Set up an isolated test environment (uv venv + local TeslaCam sample tree) and run the
# full test suite. Requires `uv` on PATH.
set -euo pipefail

ADDON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

VENV="$WORK/venv"
DATA="$WORK/data"
TESLACAM="$WORK/teslacam"
BIN="$WORK/bin"

# uv-managed virtual environment with the app deps + test client.
uv venv "$VENV" >/dev/null
VIRTUAL_ENV="$VENV" uv pip install --quiet -r "$ADDON_DIR/requirements.txt" httpx >/dev/null

# Fake ffmpeg first on PATH (real frame extraction needs a decodable video; the sample tree
# uses random bytes).
mkdir -p "$BIN"
cp "$ADDON_DIR/tests/fake_ffmpeg.py" "$BIN/ffmpeg"
chmod +x "$BIN/ffmpeg"

# Sample SavedClips event (front + back cameras, one minute).
EV="$TESLACAM/SavedClips/2024-01-15_10-30-22"
mkdir -p "$EV" "$DATA/cache"
printf '{"reason":"user_interaction_honk","city":"Seattle","est_lat":47.6,"est_lon":-122.3}' > "$EV/event.json"
printf 'PNGDATA' > "$EV/thumb.png"
head -c 4096 /dev/urandom > "$EV/2024-01-15_10-30-22-front.mp4"
head -c 4096 /dev/urandom > "$EV/2024-01-15_10-30-22-back.mp4"

# Sample RecentClips clips nested under a date sub-folder (non-flat layout) to prove the
# indexer descends into it and playback fetches by the clip's real path.
REC="$TESLACAM/RecentClips/2024-01-15"
mkdir -p "$REC"
head -c 2048 /dev/urandom > "$REC/2024-01-15_10-31-00-front.mp4"
head -c 2048 /dev/urandom > "$REC/2024-01-15_10-31-00-back.mp4"

# test_config/test_indexer/test_video_thumb/test_auth/test_upload each build their own
# isolated temp fixture internally; test_api.py uses this shared sample tree, mirroring how
# run.sh configures the real container.
for t in test_config test_indexer test_video_thumb test_auth test_upload test_serve test_port_restriction test_delete test_api; do
  echo "=== ${t} ==="
  PATH="$BIN:$PATH" PYTHONPATH="$ADDON_DIR" \
    TUV_TESLACAM_PATH="$TESLACAM" TUV_DATA_DIR="$DATA" TUV_CACHE_DIR="$DATA/cache" \
    TUV_MQTT_ENABLED=false \
    "$VENV/bin/python" "$ADDON_DIR/tests/${t}.py"
done
