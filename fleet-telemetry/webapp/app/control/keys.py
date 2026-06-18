"""EC (prime256v1) keypair management for vehicle-command signing.

The private key signs fleet_telemetry_config; the public key is registered with Tesla and served at
the .well-known path. Ported from the v0 server; the config flag (keypair_generated) is set by the
route layer, not here.
"""
import hashlib
import os
import subprocess


def key_fingerprint(private_path):
    try:
        out = subprocess.run(["openssl", "ec", "-in", private_path, "-pubout", "-outform", "DER"],
                             capture_output=True, timeout=10)
        if out.returncode == 0:
            return hashlib.sha256(out.stdout).hexdigest()[:32]
    except Exception:
        pass
    return ""


def public_key_pem(public_path):
    try:
        with open(public_path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def generate_keypair(private_path, public_path, force=False):
    if os.path.exists(private_path) and not force:
        return {"ok": True, "already": True, "fingerprint": key_fingerprint(private_path)}
    os.makedirs(os.path.dirname(private_path) or ".", exist_ok=True)
    try:
        subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout",
                        "-out", private_path], check=True, capture_output=True, timeout=15)
        os.chmod(private_path, 0o600)
        subprocess.run(["openssl", "ec", "-in", private_path, "-pubout", "-out", public_path],
                       check=True, capture_output=True, timeout=15)
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": f"openssl failed: {(e.stderr or b'').decode('utf-8', 'replace')[:200]}"}
    except Exception as e:
        return {"ok": False, "error": f"keypair generation failed: {e}"}
    return {"ok": True, "fingerprint": key_fingerprint(private_path)}
