"""Phase 3 — wizard config load/save/redact/mask-strip."""
import importlib
import json

config = importlib.import_module("app.control.config")


def test_load_returns_defaults_when_absent(tmp_path):
    cfg = config.load(str(tmp_path / "nope.json"))
    assert cfg["server"]["namespace"] == "tesla_telemetry"
    assert cfg["tesla"]["region"] == "na"


def test_save_deep_merges_and_round_trips(tmp_path):
    p = str(tmp_path / "wizard-config.json")
    config.save(p, {"tesla": {"client_id": "abc"}})
    config.save(p, {"tesla": {"region": "eu"}, "teslamate": {"bridge_enabled": True}})
    cfg = config.load(p)
    assert cfg["tesla"]["client_id"] == "abc"          # preserved across merges
    assert cfg["tesla"]["region"] == "eu"
    assert cfg["teslamate"]["bridge_enabled"] is True
    # written file is not the full defaults — only chosen keys
    on_disk = json.load(open(p))
    assert "server" not in on_disk


def test_redacted_masks_set_secrets(tmp_path):
    p = str(tmp_path / "c.json")
    config.save(p, {"tesla": {"client_secret": "s3cret"}, "npm": {"password": "pw"}})
    red = config.redacted(p)
    assert red["tesla"]["client_secret"] == config.SECRET_MASK
    assert red["npm"]["password"] == config.SECRET_MASK
    assert red["tesla"]["client_id"] == ""             # empty secret not masked


def test_strip_secret_masks_drops_sentinel():
    patch = {"tesla": {"client_secret": config.SECRET_MASK, "client_id": "keep"}, "npm": {"password": "new"}}
    config.strip_secret_masks(patch)
    assert "client_secret" not in patch["tesla"]       # unchanged sentinel dropped
    assert patch["tesla"]["client_id"] == "keep"
    assert patch["npm"]["password"] == "new"           # real new secret kept
