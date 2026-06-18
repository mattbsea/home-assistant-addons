"""Fleet-API REST shim as a Starlette app over the unified Store.

Ports the v0 shim's HTTP surface (identity synthesis, readiness, the vehicle_data/products/token
routes) but reads vehicle data from the shared Store via shim_data instead of its own tail+state.
Priming (the optional Fleet-API cold-start snapshot) is driven by the control plane, which sets
``Registry.set_prime`` — kept out of the request path.
"""
import hashlib
import time

import fields
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.sinks import shim_data

ONLINE_WINDOW = 660  # seconds; matches the v0 shim


def synth_id(vin, salt):
    """Deterministic, stable id from a VIN, kept < 2^53 so JSON float parsers can't round it."""
    return int(hashlib.sha1((salt + vin).encode()).hexdigest()[:13], 16)


class Registry:
    def __init__(self, store):
        self.store = store

    def _m(self, vin):
        """Compatibility view over the Store, which now owns prime/tesla_id/display_name."""
        v = self.store.vehicles.get(vin) or {}
        return {"prime": v.get("prime"), "tesla_id": v.get("tesla_id"),
                "display_name": v.get("display_name"), "charge_baseline": None}

    def set_prime(self, vin, prime, tesla_id=None, display_name=None):
        self.store.set_prime(vin, prime, tesla_id=tesla_id, display_name=display_name)

    def vins(self):
        return sorted(self.store.vins())

    def display_name(self, vin):
        return self.store.display_name(vin)

    def ready(self, vin):
        if self.store.get_prime(vin):
            return True
        f = self.store.snapshot(vin)
        has_batt = fields.num(f.get("Soc")) is not None or fields.num(f.get("BatteryLevel")) is not None
        return has_batt and fields.parse_location(f.get("Location"))[0] is not None

    def state_str(self, vin):
        v = self.store.vehicles.get(vin)
        last = v["last_epoch"] if v else 0.0
        fresh = last and (time.time() - last) < ONLINE_WINDOW
        return "online" if (fresh and self.ready(vin)) else "asleep"

    def identity(self, vin):
        return {"id": synth_id(vin, "id:"), "vehicle_id": synth_id(vin, "vid:"), "vin": vin,
                "state": self.state_str(vin), "display_name": self.display_name(vin), "in_service": False}

    def by_ext_id(self, ext_id):
        for vin in self.vins():
            if synth_id(vin, "id:") == ext_id:
                return vin
        return None

    def vehicle_data(self, vin):
        # All state now lives in the Store: live telemetry snapshot, the Fleet-API prime, and the
        # live charge baseline. The ephemeral-safe merge happens in shim_data.vehicle_data.
        return shim_data.vehicle_data(self.store.snapshot(vin), ts=int(time.time() * 1000),
                                      identity=self.identity(vin),
                                      charge_baseline=self.store.charge_baseline(vin),
                                      prime=self.store.get_prime(vin))


def build_app(store, registry):
    def _list(_req):
        lst = [registry.identity(v) for v in registry.vins()]
        return JSONResponse({"response": lst, "count": len(lst)})

    def _one(req):
        vin = registry.by_ext_id(int(req.path_params["eid"]))
        if vin is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse({"response": registry.identity(vin)})

    def _vehicle_data(req):
        vin = registry.by_ext_id(int(req.path_params["eid"]))
        if vin is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if not registry.ready(vin):
            return JSONResponse({"error": "vehicle unavailable: data not ready", "error_description": ""},
                                status_code=408)
        return JSONResponse({"response": registry.vehicle_data(vin)})

    async def _token(_req):
        # Lets TeslaMate "refresh" against us forever; the opaque qts- prefix makes it skip JWT decode.
        return JSONResponse({"access_token": "qts-shim-token", "token_type": "Bearer", "expires_in": 28800,
                             "refresh_token": "shim-refresh-token", "created_at": int(time.time()),
                             "id_token": "qts-shim-id-token"})

    return Starlette(routes=[
        Route("/api/1/products", _list),
        Route("/api/1/vehicles", _list),
        Route("/api/1/vehicles/{eid:int}", _one),
        Route("/api/1/vehicles/{eid:int}/vehicle_data", _vehicle_data),
        Route("/oauth2/v3/token", _token, methods=["POST"]),
    ])
