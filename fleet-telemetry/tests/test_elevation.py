"""Elevation DEM lookup — pure tile math + the on-demand Resolver (with an injected downloader so
no network is touched)."""
import array
import gzip
import importlib
import struct

import pytest

elevation = importlib.import_module("elevation")


# --- pure: tile naming / urls --------------------------------------------------------
@pytest.mark.parametrize("lat,lon,name", [
    (47.77, -122.15, "N47W123"),   # Pacific NW: floor(lon=-122.15) = -123 -> W123
    (47.0, 5.0, "N47E005"),
    (-1.5, 30.5, "S02E030"),       # floor(-1.5) = -2 -> S02
    (0.5, 0.5, "N00E000"),
    (-0.1, -0.1, "S01W001"),
])
def test_tile_name(lat, lon, name):
    assert elevation.tile_name(lat, lon) == name


def test_tile_url_groups_by_lat_band():
    assert elevation.tile_url("N47W123") == \
        "https://s3.amazonaws.com/elevation-tiles-prod/skadi/N47/N47W123.hgt.gz"


# --- pure: hgt parse + bilinear interpolation ----------------------------------------
def _hgt_bytes(values):
    """Pack a flat list of ints as big-endian int16 (HGT wire format)."""
    return struct.pack(">" + "h" * len(values), *values)


def test_parse_hgt_roundtrip_byteorder():
    grid, samples = elevation.parse_hgt(_hgt_bytes([0, 1, 2, 3, 4, 5, 6, 7, 8]))
    assert samples == 3
    assert list(grid) == [0, 1, 2, 3, 4, 5, 6, 7, 8]   # big-endian decoded regardless of host


# 3x3 tile over lat [47,48], lon [5,6]; row 0 = NORTH edge, row 2 = SOUTH edge.
#   grid =  N: 0 1 2
#              3 4 5
#           S: 6 7 8
_GRID = array.array("h", [0, 1, 2, 3, 4, 5, 6, 7, 8])


# Coordinates exactly on the N/E edge (lat=48 / lon=6) floor into the *next* tile, so interior
# fractions are used to address the N47E005 grid unambiguously.
@pytest.mark.parametrize("lat,lon,expected,tol", [
    (47.0, 5.0, 6.0, 1e-6),       # SW corner
    (47.0, 5.5, 7.0, 1e-6),       # mid south edge
    (47.5, 5.0, 3.0, 1e-6),       # mid west edge
    (47.5, 5.5, 4.0, 1e-6),       # dead center
    (47.999, 5.0, 0.0, 0.01),     # ~NW corner
    (47.999, 5.999, 2.0, 0.02),   # ~NE corner
])
def test_elevation_at_corners_and_center(lat, lon, expected, tol):
    assert elevation.elevation_at(_GRID, 3, lat, lon) == pytest.approx(expected, abs=tol)


def test_elevation_at_void_returns_none():
    voids = array.array("h", [elevation.VOID] * 9)
    assert elevation.elevation_at(voids, 3, 47.5, 5.5) is None


def test_elevation_at_partial_void_averages_present():
    #   N: V V V        cell at rows{1,2} cols{0,1} -> corners V,100,V,300 ; present avg = 200
    #      V 100 200
    #   S: V 300 400
    g = array.array("h", [elevation.VOID, elevation.VOID, elevation.VOID,
                          elevation.VOID, 100, 200, elevation.VOID, 300, 400])
    assert elevation.elevation_at(g, 3, 47.25, 5.25) == pytest.approx(200.0)


# --- Resolver: sync lookup + background fetch (injected downloader, no network) -------
def test_elevation_none_when_tile_absent_and_no_loop():
    r = elevation.Resolver("/tmp/ft-elev-test-x", enabled=True)
    # no running event loop -> _schedule is a no-op, returns None without raising
    assert r.elevation(47.77, -122.15) is None


def test_elevation_uses_preloaded_tile():
    r = elevation.Resolver("/tmp/ft-elev-test-x", enabled=False)
    r.tiles["N47E005"] = (_GRID, 3)
    assert r.elevation(47.5, 5.5) == 4      # rounded meters from the cached grid


async def test_ensure_downloads_parses_and_caches(tmp_path):
    captured = {}

    async def fake_downloader(url):
        captured["url"] = url
        return gzip.compress(_hgt_bytes([0, 1, 2, 3, 4, 5, 6, 7, 8]))

    r = elevation.Resolver(str(tmp_path), downloader=fake_downloader)
    await r.ensure("N47E005")
    assert captured["url"].endswith("/N47/N47E005.hgt.gz")
    assert "N47E005" in r.tiles and "N47E005" not in r.pending
    assert r.elevation(47.5, 5.5) == 4
    assert (tmp_path / "N47E005.hgt").exists()           # persisted to the volume


async def test_ensure_marks_missing_on_not_found(tmp_path):
    async def not_found(url):
        return None                                       # downloader signals 404/absent

    r = elevation.Resolver(str(tmp_path), downloader=not_found)
    await r.ensure("S90E000")
    assert "S90E000" in r.missing
    # a missing tile is never rescheduled
    assert r.elevation(-89.5, 0.5) is None and "S90E000" in r.missing


async def test_ensure_transient_error_allows_retry(tmp_path):
    async def boom(url):
        raise OSError("connection reset")

    r = elevation.Resolver(str(tmp_path), downloader=boom)
    await r.ensure("N47E005")
    assert "N47E005" not in r.missing                    # transient -> retryable
    assert "N47E005" not in r.pending


def test_load_disk_reads_cached_tiles(tmp_path):
    (tmp_path / "N47E005.hgt").write_bytes(_hgt_bytes([0, 1, 2, 3, 4, 5, 6, 7, 8]))
    r = elevation.Resolver(str(tmp_path), enabled=False)
    r.load_disk()
    assert r.elevation(47.5, 5.5) == 4


# --- ElevationSmoother: causal EMA that removes jitter×slope noise (inflates TeslaMate ascent) ---
def test_elevation_smoother_ema_reset_and_passthrough():
    sm = elevation.ElevationSmoother(alpha=0.5, gap_reset_s=60)
    assert sm.smooth("V", 100.0, ts=0) == 100.0          # first sample seeds (passthrough)
    assert sm.smooth("V", 200.0, ts=1) == 150.0          # 0.5*200 + 0.5*100
    assert sm.smooth("V", None, ts=2) is None            # None -> None, state unchanged
    assert sm.smooth("V", 200.0, ts=3) == 175.0          # 0.5*200 + 0.5*150 (continues from 150)
    assert sm.smooth("W", 10.0, ts=3) == 10.0            # per-VIN independent
    assert sm.smooth("V", 50.0, ts=200) == 50.0          # gap > gap_reset_s -> reset (re-seed)


def test_elevation_smoother_alpha_one_disables():
    sm = elevation.ElevationSmoother(alpha=1.0)
    assert sm.smooth("V", 100.0, ts=0) == 100.0
    assert sm.smooth("V", 200.0, ts=1) == 200.0          # no smoothing


def test_elevation_smoother_seeds_from_last_on_repeat_position():
    """The sink broadcasts on every field change, so many broadcasts land at the SAME location
    (PackCurrent/Soc tick between GPS updates). Re-running the EMA on a repeated position re-converges
    it toward the raw DEM there, weakening the smoothing and inflating ascent. So a repeated
    (lat, lon) must seed-from-last (return the prior value, no advance); only a real move advances."""
    sm = elevation.ElevationSmoother(alpha=0.5, gap_reset_s=60)
    assert sm.smooth("V", 100.0, ts=0, lat=47.0, lon=-122.0) == 100.0   # first position seeds
    # same position, different reading (would be 150 if it advanced) -> held at the seed
    assert sm.smooth("V", 200.0, ts=1, lat=47.0, lon=-122.0) == 100.0
    assert sm.smooth("V", 200.0, ts=2, lat=47.0, lon=-122.0) == 100.0
    # a genuine move advances the EMA: 0.5*200 + 0.5*100 = 150
    assert sm.smooth("V", 200.0, ts=3, lat=47.1, lon=-122.1) == 150.0
    # ...and the new position then becomes the "last" for dedup
    assert sm.smooth("V", 999.0, ts=4, lat=47.1, lon=-122.1) == 150.0


def test_elevation_resolver_round_m_false_returns_unrounded_float():
    """The smoother must filter the float DEM and round once at the end; rounding to integer meters
    *before* smoothing bakes in quantization noise. round_m=False exposes the unrounded value."""
    r = elevation.Resolver("/tmp/ft-elev-test-x", enabled=False)
    r.tiles["N47E005"] = (_GRID, 3)
    v = r.elevation(47.3, 5.2, round_m=False)
    assert v == pytest.approx(4.6)        # genuinely fractional interpolation
    assert r.elevation(47.3, 5.2) == 5    # default still rounds to int
