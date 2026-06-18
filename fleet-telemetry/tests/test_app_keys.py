"""Phase 3 — EC keypair generation (control plane)."""
import importlib

keys = importlib.import_module("app.control.keys")


def test_generate_and_idempotent_and_force(tmp_path):
    priv = str(tmp_path / "private-key.pem")
    pub = str(tmp_path / "public-key.pem")

    r1 = keys.generate_keypair(priv, pub)
    assert r1["ok"] and len(r1["fingerprint"]) == 32
    assert keys.public_key_pem(pub).startswith(b"-----BEGIN PUBLIC KEY-----")
    fp1 = r1["fingerprint"]

    # idempotent without force
    r2 = keys.generate_keypair(priv, pub)
    assert r2.get("already") is True and r2["fingerprint"] == fp1

    # force regenerates a different key
    r3 = keys.generate_keypair(priv, pub, force=True)
    assert r3["ok"] and r3["fingerprint"] != fp1
