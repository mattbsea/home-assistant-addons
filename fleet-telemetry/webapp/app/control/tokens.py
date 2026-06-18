"""Operational refresh-token storage in the shim-state file (NOT the wizard config).

Tesla rotates the refresh token on every use (priming, send-config). That rotation must NOT be
written to wizard-config.json: run.sh watches that file as its restart signal, so persisting a
rotated token there would bounce the fleet-telemetry binary and drop the vehicle's connection.
The shim-state file is unwatched, so the token rotates freely without disrupting telemetry.
(wizard-config.json's shim_refresh_token remains only the initial OAuth seed.)
"""
import json
import os


def read_state(state_path):
    """The whole shim-state dict (refresh_token + any operational keys like the roster hash)."""
    try:
        with open(state_path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_state(state_path, **updates):
    """Merge keys into shim-state atomically. Never write to the watched wizard-config.json."""
    data = read_state(state_path)
    data.update(updates)
    try:
        with open(state_path + ".tmp", "w") as fh:
            json.dump(data, fh)
        os.replace(state_path + ".tmp", state_path)
    except OSError:
        pass


def load(state_path):
    return read_state(state_path).get("refresh_token", "")


def save(state_path, rt):
    write_state(state_path, refresh_token=rt)
