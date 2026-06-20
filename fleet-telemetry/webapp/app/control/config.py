"""Wizard configuration: the authoritative settings file the wizard writes and run.sh reads.

Ported from the v0 server: deep-merge over defaults, atomic chmod-600 writes, and secret masking
(the UI gets a sentinel for set secrets and omits unchanged ones from save patches).
"""
import copy
import json
import os
import tempfile

CONFIG_DEFAULTS = {
    "version": 1,
    "tesla": {
        "client_id": "", "client_secret": "", "region": "na",
        "pubkey_domain": "", "telemetry_domain": "", "telemetry_port": 4443,
        "shim_refresh_token": "", "partner_registered": False, "keypair_generated": False,
    },
    "npm": {
        "url": "", "email": "", "password": "", "cert_domain": "",
        "forward_host": "", "cert_refresh_hours": 12, "pubkey_proxy_host_id": None, "stream_id": None,
    },
    "server": {
        "log_level": "info", "json_log_enable": True, "namespace": "tesla_telemetry",
        "reliable_ack": False, "rate_limit_enabled": True,
        "rate_limit_message_interval": 30, "rate_limit_message_limit": 1000,
        "metrics_enabled": False, "extra_config_json": "",
    },
    "backends": {
        "logger": True,
        "mqtt": {"enabled": False, "broker": "", "client_id": "fleet-telemetry",
                 "topic_base": "telemetry", "qos": 1, "username": "", "password": ""},
        "pubsub": {"enabled": False, "gcp_project_id": "", "service_account_json": ""},
    },
    "teslamate": {"bridge_enabled": False, "bridge_url": ""},
}

SECRET_MASK = "__SET__"
SECRET_PATHS = (
    ("tesla", "client_secret"), ("tesla", "shim_refresh_token"),
    ("npm", "password"), ("backends", "mqtt", "password"),
    ("backends", "pubsub", "service_account_json"),
)


def deep_merge(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path):
    cfg = copy.deepcopy(CONFIG_DEFAULTS)
    try:
        with open(path) as fh:
            deep_merge(cfg, json.load(fh) or {})
    except (OSError, ValueError):
        pass
    return cfg


def _read_raw(path):
    try:
        with open(path) as fh:
            cur = json.load(fh) or {}
    except (OSError, ValueError):
        cur = {}
    return cur if isinstance(cur, dict) else {}


def _atomic_write(path, current):
    dirpath = os.path.dirname(path) or "."
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(current, fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return load(path)


def save(path, patch):
    current = _read_raw(path)
    deep_merge(current, patch)
    return _atomic_write(path, current)


def redacted(path):
    cfg = copy.deepcopy(load(path))
    for p in SECRET_PATHS:
        d = cfg
        for k in p[:-1]:
            d = d.get(k, {}) if isinstance(d, dict) else {}
        last = p[-1]
        if isinstance(d, dict) and d.get(last):
            d[last] = SECRET_MASK
    return cfg


def load_wizard_state(path):
    """The wizard's step-progress state (separate from the settings config)."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_wizard_state(path, patch):
    state = load_wizard_state(path)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(state.get(k), dict):
            state[k] = {**state[k], **v}
        else:
            state[k] = v
    dirpath = os.path.dirname(path) or "."
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return state


def strip_secret_masks(patch):
    """Drop secret fields whose value is the unchanged sentinel (in place)."""
    for p in SECRET_PATHS:
        d = patch
        ok = True
        for k in p[:-1]:
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                ok = False
                break
        last = p[-1]
        if ok and isinstance(d, dict) and d.get(last) == SECRET_MASK:
            del d[last]
    return patch
