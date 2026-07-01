#!/usr/bin/env bash
# Registers (if not already registered) one GitHub Actions runner for a single target
# (repo or org). Idempotent: if this target's runner already has local credentials
# (${runner_home}/.runner), it skips re-registration entirely rather than minting a fresh
# GitHub API token on every restart. Does not start run.sh — the caller does that.
#
# Usage: register-target.sh <name> <scope: repo|org> <url> <token> <labels> <work_dir>
set -u

name="$1"; scope="$2"; url="$3"; token="$4"; labels="$5"; work_dir="$6"

runner_home="${work_dir}/runner"

if [ -f "${runner_home}/.runner" ]; then
    echo "[${name}] already registered (found ${runner_home}/.runner) — skipping re-registration"
    exit 0
fi

api_base="https://api.github.com"
case "${scope}" in
    repo) reg_token_url="${api_base}/repos/${url}/actions/runners/registration-token" ;;
    org)  reg_token_url="${api_base}/orgs/${url}/actions/runners/registration-token" ;;
    *)
        echo "[${name}] unknown scope '${scope}' (expected 'repo' or 'org')" >&2
        exit 1
        ;;
esac

reg_token="$(curl -sf -X POST \
    -H "Authorization: Bearer ${token}" \
    -H "Accept: application/vnd.github+json" \
    "${reg_token_url}" | jq -r '.token // empty')"

if [ -z "${reg_token}" ]; then
    echo "[${name}] failed to obtain a registration token from ${reg_token_url} — check the PAT's scope/permissions" >&2
    exit 1
fi

mkdir -p "${runner_home}"
cp -r /opt/actions-runner/. "${runner_home}/"

export RUNNER_ALLOW_RUNASROOT="1"
( cd "${runner_home}" && ./config.sh \
    --url "https://github.com/${url}" \
    --token "${reg_token}" \
    --name "ha-${name}" \
    --labels "self-hosted,linux,x64,docker${labels:+,${labels}}" \
    --work "${work_dir}/_work" \
    --unattended \
    --replace )
