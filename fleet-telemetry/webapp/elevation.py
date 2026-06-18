"""Elevation lookup for the TeslaMate streaming `elevation` column.

Tesla's Fleet API and Fleet Telemetry expose NO elevation/altitude (the proto `Location` is lat/long
only; `vehicle_data` has no elevation) — that data was carried by the legacy owner-api streaming
WebSocket that the Fleet generation replaced. TeslaMate's stream parser still expects an `elevation`
column, so we fill it ourselves from a local DEM.

Source: AWS public "terrain-tiles" skadi dataset (no auth) — gzipped 1°×1° SRTM-derived HGT tiles
(16-bit big-endian meters, WGS84). Only the tiles for regions actually driven are fetched, gunzipped,
parsed in pure Python (no GDAL), and cached to the add-on's persistent `/data` volume — so after the
first fetch in a region lookups are fully offline, rate-limit-free, and coordinates never leave the box.

The `Resolver` lookup is synchronous and non-blocking: it returns the cached elevation or None and
schedules the tile download in the background, so the streaming frame is never delayed (the first
frame at a brand-new 1° cell gets blank elevation, filled one frame later once the tile lands).
"""
import array
import asyncio
import gzip
import math
import os
import sys
import urllib.error
import urllib.request

VOID = -32768  # SRTM no-data marker
SKADI_BASE = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"


def tile_name(lat, lon):
    """1° tile name for a coordinate, e.g. (47.77, -122.15) -> 'N47W123' (SW-corner integer floor)."""
    la, lo = int(math.floor(lat)), int(math.floor(lon))
    return f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}"


def tile_url(name, base=SKADI_BASE):
    """Skadi URL for a tile; tiles are grouped under their latitude band dir (the first 3 chars)."""
    return f"{base}/{name[:3]}/{name}.hgt.gz"


def parse_hgt(raw):
    """Parse raw (gunzipped) HGT bytes into a flat big-endian-corrected int16 array + side length.
    HGT is a square grid of 16-bit big-endian samples, row-major from the NW corner (N→S, W→E)."""
    a = array.array("h")
    a.frombytes(raw)
    if sys.byteorder == "little":      # HGT is big-endian; swap on little-endian hosts
        a.byteswap()
    samples = int(round(len(a) ** 0.5))
    return a, samples


def elevation_at(grid, samples, lat, lon):
    """Bilinearly interpolate meters at (lat, lon) within a tile grid. Row 0 is the NORTH edge.
    Returns None if the surrounding cell is entirely void; averages partial voids (coastlines)."""
    lat_f, lon_f = lat - math.floor(lat), lon - math.floor(lon)
    y = (1.0 - lat_f) * (samples - 1)   # 0 at north edge, grows southward
    x = lon_f * (samples - 1)           # 0 at west edge, grows eastward
    r0, c0 = int(math.floor(y)), int(math.floor(x))
    r0 = max(0, min(r0, samples - 1))
    c0 = max(0, min(c0, samples - 1))
    r1, c1 = min(r0 + 1, samples - 1), min(c0 + 1, samples - 1)
    dy, dx = y - r0, x - c0

    def g(r, c):
        v = grid[r * samples + c]
        return None if v == VOID else float(v)

    corners = [g(r0, c0), g(r0, c1), g(r1, c0), g(r1, c1)]
    if any(v is None for v in corners):
        good = [v for v in corners if v is not None]
        return sum(good) / len(good) if good else None
    v00, v01, v10, v11 = corners
    top = v00 * (1 - dx) + v01 * dx
    bot = v10 * (1 - dx) + v11 * dx
    return top * (1 - dy) + bot * dy


def _round_m(v):
    return None if v is None else int(round(v))


class Resolver:
    """On-demand DEM tile cache. `elevation()` is sync + non-blocking; downloads happen in the
    background on the running event loop and persist to `cache_dir` (on `/data`)."""

    def __init__(self, cache_dir, *, enabled=True, downloader=None, base=SKADI_BASE):
        self.dir = cache_dir
        self.enabled = enabled
        self.base = base
        self._downloader = downloader      # test seam: async (url) -> bytes|None (None = not found)
        self.tiles = {}                    # name -> (grid, samples)
        self.missing = set()               # names known absent (ocean / 404) — never re-fetched
        self.pending = set()               # names with an in-flight download
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except OSError:
            pass

    def load_disk(self):
        """Preload any tiles already cached on the persistent volume (survives reboots/upgrades)."""
        try:
            names = os.listdir(self.dir)
        except OSError:
            return
        for fn in names:
            if not fn.endswith(".hgt"):
                continue
            try:
                with open(os.path.join(self.dir, fn), "rb") as fh:
                    self.tiles[fn[:-4]] = parse_hgt(fh.read())
            except OSError:
                pass

    def elevation(self, lat, lon):
        """Meters at (lat, lon) if the tile is cached, else None (scheduling a background fetch)."""
        if lat is None or lon is None:
            return None
        name = tile_name(lat, lon)
        t = self.tiles.get(name)
        if t is not None:
            return _round_m(elevation_at(t[0], t[1], lat, lon))
        if self.enabled and name not in self.missing and name not in self.pending:
            self._schedule(name)
        return None

    def _schedule(self, name):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return                          # no loop (sync test / startup) — skip background fetch
        self.pending.add(name)
        loop.create_task(self.ensure(name))

    async def ensure(self, name):
        """Download + cache one tile. Marks `missing` only on a definitive not-found (404/403/empty);
        transient errors just clear `pending` so the next frame retries."""
        if name in self.tiles or name in self.missing:
            self.pending.discard(name)
            return
        self.pending.add(name)
        try:
            raw_gz = await self._download(tile_url(name, self.base))
        except Exception:                   # transient (network) — allow a later retry
            self.pending.discard(name)
            return
        try:
            if not raw_gz:
                self.missing.add(name)      # definitively absent (e.g. ocean tile)
                return
            raw = gzip.decompress(raw_gz)
            self.tiles[name] = parse_hgt(raw)
            try:
                with open(os.path.join(self.dir, name + ".hgt"), "wb") as fh:
                    fh.write(raw)
            except OSError:
                pass
        finally:
            self.pending.discard(name)

    async def _download(self, url):
        if self._downloader is not None:
            return await self._downloader(url)

        def fetch():
            req = urllib.request.Request(url, headers={"User-Agent": "fleet-telemetry-addon"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code in (403, 404):
                    return None             # not found -> permanent missing
                raise                        # other HTTP error -> transient
        return await asyncio.to_thread(fetch)
