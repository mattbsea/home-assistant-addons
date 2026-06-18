"""Operational refresh-token storage in the shim-state file (NOT the wizard config).

Tesla rotates the refresh token on every use (priming, send-config). That rotation must NOT be
written to wizard-config.json: run.sh watches that file as its restart signal, so persisting a
rotated token there would bounce the fleet-telemetry binary and drop the vehicle's connection.
The shim-state file is unwatched, so the token rotates freely without disrupting telemetry.
(wizard-config.json's shim_refresh_token remains only the initial OAuth seed.)
"""
import json
import os


def load(state_path):
    try:
        with open(state_path) as fh:
            return json.load(fh).get("refresh_token", "")
    except (OSError, ValueError):
        return ""


def save(state_path, rt):
    try:
        data = {}
        try:
            with open(state_path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        data["refresh_token"] = rt
        with open(state_path + ".tmp", "w") as fh:
            json.dump(data, fh)
        os.replace(state_path + ".tmp", state_path)
    except OSError:
        pass
