#!/usr/bin/env bash
# Print the secrets the app stored in keys.json. Run inside the add-on container:
#   docker exec <container> show-keys
# Keys given in the Configuration tab (mcp_key, shortcut_key) override these and are not stored here.
set -eu

DATA_DIR="${FOOD_TRACKER_DATA_DIR:-/data}"
FILE="${DATA_DIR}/keys.json"

if [ ! -f "${FILE}" ]; then
    echo "No ${FILE} yet: start the add-on once so it can generate the keys." >&2
    exit 1
fi

echo "File: ${FILE}"
# Not export_secret: that signs download links and is not for sharing.
# shellcheck disable=SC2016  # the program is JavaScript; ${...} must reach Bun unexpanded
bun -e '
const keys = JSON.parse(await Bun.file(process.argv[1]).text());
for (const name of ["mcp_key", "shortcut_key"]) {
    console.log(`${name}: ${typeof keys[name] === "string" ? keys[name] : "(not stored; it comes from the add-on Configuration)"}`);
}
' "${FILE}"
