"""Resolve the host-side port the add-on is mapped to (NPM forwards to the HOST port, which the user
can remap in the Network tab), via the Supervisor API. Falls back to the internal port."""
import json
import os
import urllib.request


def addon_host_port(internal_port):
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        return internal_port
    try:
        req = urllib.request.Request("http://supervisor/addons/self/info",
                                     headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read().decode("utf-8"))
        net = (body.get("data") or {}).get("network") if isinstance(body, dict) else None
        mapped = net.get(f"{internal_port}/tcp") if isinstance(net, dict) else None
        if isinstance(mapped, int) and mapped > 0:
            return mapped
    except Exception:
        pass
    return internal_port
