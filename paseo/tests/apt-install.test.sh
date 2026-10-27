#!/usr/bin/env bash
# Unit tests for apt-install.sh name validation, with a fake apt-get. Run: bash paseo/tests/apt-install.test.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
cat > "${TMP}/apt-get" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >> "${CALLS}"
[ -z "${FAIL:-}" ]
FAKE
chmod +x "${TMP}/apt-get"
export CALLS="${TMP}/calls" APT_GET="${TMP}/apt-get"
fails=0

# run <expected-exit> <expected-install-args-or-empty> <json>
run() {
    local want_rc="$1" want_calls="$2" json="$3" rc got
    : > "${CALLS}"
    printf '%s' "${json}" > "${TMP}/o.json"
    "${HERE}/../apt-install.sh" "${TMP}/o.json" >/dev/null 2>&1
    rc=$?
    got="$(grep '^install' "${CALLS}" | sed 's/^install -y --no-install-recommends //')"
    if [ "${rc}" = "${want_rc}" ] && [ "${got}" = "${want_calls}" ]; then
        echo "ok   ${json}"
    else
        echo "FAIL ${json}: rc=${rc} (want ${want_rc}), installed='${got}' (want '${want_calls}')"
        fails=$((fails + 1))
    fi
}

run 0 "" '{}'
run 0 "" '{"apt_packages":[]}'
run 0 "kicad g++ libstdc++6 python3.11 gcc-12" '{"apt_packages":["kicad","g++","libstdc++6","python3.11","gcc-12"]}'
run 1 "kicad" '{"apt_packages":["kicad","x; rm -rf /"]}'
run 1 "" '{"apt_packages":["$(id)"]}'
run 1 "" '{"apt_packages":["-oAPT::Update::Pre-Invoke::=id"]}'
run 1 "" '{"apt_packages":["Kicad","a","a b","a\nb","pkg=1.0","pkg/stable","`id`","a|b","a&b"]}'
run 0 "" '{"apt_packages":[""]}'
FAIL=1 run 1 "" '{"apt_packages":["kicad"]}'

[ "${fails}" = 0 ] && echo "all passed" || { echo "${fails} failed"; exit 1; }
