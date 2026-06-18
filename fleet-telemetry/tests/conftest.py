"""Test harness for the Fleet Telemetry add-on webapp.

Phase 0 characterization: these fixtures + tests pin the *current* (v0.10.16) behavior of the
transforms so the v1 refactor (shared fields.py / records.py) can be proven equivalent.

Env is neutralized BEFORE importing the webapp modules so their module-level constants/state
(e.g. shim's `MGR = Manager()`) never touch real /data files.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEBAPP = HERE.parent / "webapp"
FIXTURES = HERE / "fixtures"

# Point every file/env the modules read at harmless, nonexistent temp paths before import.
os.environ.setdefault("FT_SHIM_STATE", "/tmp/ft-test-shim-state.json")
os.environ.setdefault("FT_RECORDS_FILE", "/tmp/ft-test-records.jsonl")
os.environ.setdefault("FT_WIZARD_CONFIG", "/tmp/ft-test-wizard-config.json")
os.environ.setdefault("FT_WIZARD_STATE", "/tmp/ft-test-wizard-state.json")
os.environ.setdefault("FT_CERT_FILE", "/tmp/ft-test-cert.pem")

if str(WEBAPP) not in sys.path:
    sys.path.insert(0, str(WEBAPP))


def load_records():
    """The curated real telemetry record_payload lines used across tests."""
    with open(FIXTURES / "records.jsonl") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_reference(name):
    with open(FIXTURES / "reference" / name) as fh:
        return json.load(fh)
