"""Offline basemap: projection maths, the tile pack's provenance and the fallback rules.

Three things this suite exists to keep true.

1. The geometry. A tile whose mercator maths is off by one row puts streets under the
   wrong search area, and the error is invisible on screen.
2. The honesty of provenance. A station with no tile pack must say `procedural`, a
   corrupt manifest must not take the map down, and a rebuilt manifest must be picked up
   without a restart — the operator's picture is labelled by these values.
3. The over-zoom rule. Above the pack's top zoom the endpoint must return an enlarged
   real tile. Falling through to the procedural field would hand the operator invented
   terrain at exactly the zoom where they are looking closest.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(module_name: str, relative: str):
    """Import a `tools/` script as a module without touching the packaged namespace."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, REPO / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


basemap_tool = _load("osm_basemap", "tools/osm_basemap.py")
offline_map = _load("offline_map", "tools/offline_map.py")

# The sample mission's own geography, so the maths is tested where the product is used.
PAD = (77.59910, 12.94870)


def _pad_tile(zoom: int) -> tuple[int, int, int]:
    """The (z, x, y) tile that contains the launch pad."""
    x, y = basemap_tool.tiles_for_bounds(PAD[0], PAD[1], PAD[0], PAD[1], zoom)[0]
    return zoom, x, y


def _pixel_of(zoom: int, x: int, y: int, lon: float, lat: float) -> tuple[int, int]:
    """Where a coordinate lands inside a tile, using the renderer's own transform.

    `tile_bounds` answers in degrees and the layer arrays hold metres, so the conversion
    is the whole trick — and the one place a wrong sign would put streets in the
    neighbouring tile.
    """
    west, _south, _east, north = basemap_tool.tile_bounds(zoom, x, y)
    west_m, north_m = basemap_tool.lnglat_to_mercator(west, north)
    point_x, point_y = basemap_tool.lnglat_to_mercator(lon, lat)
    res = ((2 * basemap_tool.SEMICYCLE) / (2 ** zoom)) / basemap_tool.TILE_SIZE
    return int((point_x - west_m) / res), int((north_m - point_y) / res)


def _tile_centre(zoom: int, x: int, y: int) -> tuple[float, float]:
    west, south, east, north = basemap_tool.tile_bounds(zoom, x, y)
    return (west + east) / 2.0, (south + north) / 2.0


# ------------------------------------------------------------------- tile geometry
def test_mercator_corner_and_tile_bounds_agree() -> None:
    """`tile_bounds` and `lnglat_to_mercator` must describe the same rectangle.

    They are two halves of one transform: projection puts features into metres, tile
    bounds decide which features land in this tile. If they drift apart, features render
    in the neighbouring tile and the basemap silently disagrees with the mission geometry.
    """
    for z, x, y in ((12, 3660, 2306), (15, 29284, 18451), (16, 58569, 36903)):
        west, south, east, north = basemap_tool.tile_bounds(z, x, y)
        top_left = basemap_tool.lnglat_to_mercator(west, north)
        bottom_right = basemap_tool.lnglat_to_mercator(east, south)
        span = (2 * basemap_tool.SEMICYCLE) / (2 ** z)
        assert top_left[0] == pytest.approx(basemap_tool.ORIGIN + x * span, abs=1e-3)
        assert bottom_right[1] == pytest.approx(basemap_tool.ORIGIN + (2 ** z - 1 - y) * span, abs=1e-3)
        assert bottom_right[0] - top_left[0] == pytest.approx(span, rel=1e-6)
        assert top_left[1] - bottom_right[1] == pytest.approx(span, rel=1e-6)


def test_the_launch_pad_is_inside_the_tile_it_renders_in() -> None:
    """A tile must contain the point whose coordinates it is drawn from."""
    lon, lat = PAD
    for z in (12, 14, 15, 16):
        tiles = basemap_tool.tiles_for_bounds(lon, lat, lon, lat, z)
        assert len(tiles) == 1
        x, y = tiles[0]
        west, south, east, north = basemap_tool.tile_bounds(z, x, y)
        assert west <= lon <= east and south <= lat <= north


def test_a_wide_box_covers_every_tile_it_touches() -> None:
    """`tiles_for_bounds` is the culling gate for the whole pack.

    Missing a corner tile leaves a hole in the basemap at the edge of the search area,
    which reads as terrain rather than as an unbuilt tile.
    """
    west, south, east, north = offline_map.DEMO
    tiles = set(basemap_tool.tiles_for_bounds(west, south, east, north, 13))
    corners = [basemap_tool.tiles_for_bounds(lon, lat, lon, lat, 13)[0]
               for lon, lat in ((west, south), (east, south), (west, north), (east, north))]
    assert all(corner in tiles for corner in corners)
    assert len(tiles) > 4


# ---------------------------------------------------------------------- round trip
def _tiny_basemap(centre: tuple[float, float] | None = None):
    """Three features around one point: a lake, a block and a road across both.

    The default centre is the middle of the pad's z15 tile, so a pixel sampled a few
    hundred metres away is guaranteed to be inside the tile being asserted on.
    """
    cx, cy = centre if centre is not None else _tile_centre(*_pad_tile(15))
    base = basemap_tool.Basemap(region="pytest")
    base.add_polygons("water", [[(cx - 0.004, cy - 0.004), (cx + 0.004, cy - 0.004),
                                 (cx + 0.004, cy + 0.004), (cx - 0.004, cy + 0.004)]])
    base.add_polygons("residential", [[(cx - 0.001, cy - 0.001), (cx + 0.001, cy - 0.001),
                                       (cx + 0.001, cy + 0.001), (cx - 0.001, cy + 0.001)]])
    base.add_lines("road_major", [[(cx - 0.006, cy - 0.002), (cx + 0.006, cy + 0.002)]])
    return base


def test_save_and_load_reproduce_the_same_tile_byte_for_byte(tmp_path: Path) -> None:
    """The npz is the build artifact; a load that shifts a coordinate is a silent redraw."""
    base = _tiny_basemap().finalize()
    directory = tmp_path / "basemap"
    base.save(directory)
    reloaded = basemap_tool.Basemap.load(directory)
    z, x, y = _pad_tile(15)
    original = basemap_tool.render_tile(base, z, x, y).tobytes()
    again = basemap_tool.render_tile(reloaded, z, x, y).tobytes()
    assert original == again
    assert reloaded.feature_counts() == base.feature_counts()


def test_feature_counts_survive_finalisation(tmp_path: Path) -> None:
    """`feature_counts` must read the numpy index, not the lists `arrays()` released."""
    base = _tiny_basemap().finalize()
    assert base.layers["water"].coords == []
    counts = base.feature_counts()
    assert counts.get("water") == 1 and counts.get("road_major") == 1


def test_a_bucket_holds_one_geometry_kind(tmp_path: Path) -> None:
    """Water polygons and river lines cannot share a bucket; the renderer would draw one
    as the other, and a river would arrive as a filled blob."""
    base = basemap_tool.Basemap(region="pytest")
    base.add_polygons("water", [[(0.0, 0.0), (0.01, 0.0), (0.01, 0.01)]])
    with pytest.raises(ValueError):
        base.add_lines("water", [[(0.0, 0.0), (0.01, 0.01)]])


def test_adding_after_finalise_is_refused() -> None:
    """Finalising releases the Python accumulators; appending afterwards would write into
    arrays the renderer is already reading."""
    layer = basemap_tool.Layer(kind="poly")
    layer.add([(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)])
    layer.arrays()
    with pytest.raises(RuntimeError):
        layer.add([(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)])


def test_degenerate_rings_are_dropped_not_stored() -> None:
    layer = basemap_tool.Layer(kind="poly")
    layer.add([(0.0, 0.0), (1.0, 1.0)])
    layer.arrays()
    assert layer.feature_count() == 0


# -------------------------------------------------------------------------- raster
def test_render_paints_the_polygon_in_the_theme_colour() -> None:
    z, x, y = _pad_tile(15)
    centre = _tile_centre(z, x, y)
    base = _tiny_basemap(centre).finalize()
    dark = basemap_tool.render_tile(base, z, x, y, "dark")
    light = basemap_tool.render_tile(base, z, x, y, "light")
    px, py = _pixel_of(z, x, y, centre[0] - 0.0035, centre[1] - 0.0035)
    assert 0 <= px < basemap_tool.TILE_SIZE and 0 <= py < basemap_tool.TILE_SIZE
    assert dark.getpixel((px, py)) == basemap_tool.DARK["water"]
    assert light.getpixel((px, py)) == basemap_tool.LIGHT["water"]
    assert dark.getpixel((0, 0)) != light.getpixel((0, 0))


def test_features_outside_the_tile_are_culled() -> None:
    """A far-away feature must not touch a single pixel.

    Culling is a numpy bbox test over every feature in the region; if it leaked, a z10
    tile would spend its draw loop on 250k buildings off the coast.
    """
    base = basemap_tool.Basemap(region="pytest")
    far_lon, far_lat = PAD[0] + 0.5, PAD[1] + 0.5
    base.add_polygons("water", [[(far_lon, far_lat), (far_lon + 0.01, far_lat),
                                 (far_lon + 0.01, far_lat + 0.01), (far_lon, far_lat + 0.01)]])
    base.finalize()
    z, x, y = _pad_tile(15)
    painted = basemap_tool.render_tile(base, z, x, y)
    blank = basemap_tool.Image.new("RGB", (basemap_tool.TILE_SIZE, basemap_tool.TILE_SIZE),
                                   basemap_tool.DARK["background"])
    assert painted.tobytes() == blank.tobytes()


def test_road_width_is_an_integer_and_stays_capped() -> None:
    """Pillow raises on a float stroke, and an uncapped growth factor turns z16 into
    ribbons that hide the block pattern the operator is searching."""
    for zoom in range(10, 20):
        for base_zoom, base_width in ((10, 1.5), (12, 1.2), (13, 0.9), (15, 0.7)):
            width = basemap_tool._width(zoom, base_zoom, base_width)
            assert isinstance(width, int)
            assert 0 < width <= 7


def test_attribution_names_the_data_and_the_licence() -> None:
    """Whatever is on screen, the attribution has to say where it came from — and say
    plainly that the fallback is generated when nothing was surveyed."""
    lines = basemap_tool.attribution_lines({"source": {"osm_data_date": "2026-10-30T21:24:51Z"},
                                            "built_at": "2026-11-01T00:00:00Z",
                                            "layers": {"water": 100}})
    joined = " ".join(lines).lower()
    assert "openstreetmap" in joined and "odbl" in joined
    assert "2026-10-30t21:24:51z" in joined
    fallback = basemap_tool.attribution_lines(None)
    assert fallback and "procedural" in fallback[0].lower()


# ------------------------------------------------------------------- tile service
from rescueswarm.map_tiles import MAX_ZOOM, MIN_ZOOM, MapTileService  # noqa: E402


def _write_pack(root: Path, zooms: list[int], tiles: dict[tuple[int, int, int], bytes]) -> None:
    manifest = {
        "format": "rescueswarm-map-pack-v1",
        "built_at": "2026-11-01T00:00:00Z",
        "source": {"osm_data_date": "2026-10-30T21:24:51Z", "extract_file": "test.osm.pbf"},
        "coverage": {"zooms": zooms, "metro_bbox_deg": [77.40, 12.75, 77.90, 13.25]},
        "attribution": ["© OpenStreetMap contributors, ODbL"],
        "layers": {"buildings": 10},
        "simplifications": ["polygon holes are filled"],
    }
    (root / "map_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for (z, x, y), payload in tiles.items():
        directory = root / str(z) / str(x)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{y}.png").write_bytes(payload)


def test_no_pack_reports_the_procedural_surface(tmp_path: Path) -> None:
    service = MapTileService(tmp_path)
    assert service.manifest() is None
    assert service.pack_max_zoom() is None
    provenance = service.provenance()
    assert provenance["kind"] == "procedural"
    assert provenance["network_access"] is False
    assert "offline_map.py" in provenance["note"]
    # Without a pack the source must advertise the full procedural range.
    assert service.style()["sources"]["offline-terrain"]["maxzoom"] == MAX_ZOOM


def test_pack_changes_the_style_so_clients_over_zoom_instead_of_asking(tmp_path: Path) -> None:
    _write_pack(tmp_path, [10, 11, 12, 13, 14, 15, 16], {})
    service = MapTileService(tmp_path)
    provenance = service.provenance()
    assert provenance["kind"] == "openstreetmap-offline-pack"
    assert provenance["pack_zooms"] == [10, 11, 12, 13, 14, 15, 16]
    assert provenance["osm_data_date"] == "2026-10-30T21:24:51Z"
    assert provenance["network_access"] is False
    source = service.style()["sources"]["offline-terrain"]
    assert source["maxzoom"] == 16
    assert "OpenStreetMap" in source["attribution"]
    assert service.style()["metadata"]["rescue-swarm:basemap"] == "openstreetmap-offline-pack"


def test_a_corrupt_manifest_does_not_take_the_map_down(tmp_path: Path) -> None:
    """A rebuild interrupted mid-write leaves a truncated JSON file. The station still
    has to render; it must fall back and label itself procedural, not return a 500."""
    (tmp_path / "map_manifest.json").write_text("{ this is not json", encoding="utf-8")
    service = MapTileService(tmp_path)
    assert service.manifest() is None
    assert service.provenance()["kind"] == "procedural"


def test_a_rebuilt_manifest_is_picked_up_without_a_restart(tmp_path: Path) -> None:
    service = MapTileService(tmp_path)
    _write_pack(tmp_path, [12, 13, 14], {})
    assert service.provenance()["pack_zooms"] == [12, 13, 14]
    _write_pack(tmp_path, [12, 13, 14, 15, 16], {})
    path = tmp_path / "map_manifest.json"
    stat = path.stat()
    os.utime(path, (stat.st_mtime + 5, stat.st_mtime + 5))
    assert service.provenance()["pack_zooms"] == [12, 13, 14, 15, 16]
    assert service.style()["sources"]["offline-terrain"]["maxzoom"] == 16


def test_the_exact_tile_wins_over_the_pack_parent(tmp_path: Path) -> None:
    _write_pack(tmp_path, [15, 16], {(16, 58569, 36903): b"exact", (15, 29284, 18451): b"parent"})
    service = MapTileService(tmp_path)
    assert service.tile(16, 58569, 36903) == b"exact"


def test_over_zoom_returns_the_real_parent_and_not_invented_terrain(tmp_path: Path) -> None:
    """z17 is above the pack. The operator is closest to the ground here, so substituting
    the procedural field would be the worst possible place to fabricate a street."""
    _write_pack(tmp_path, [15, 16], {(16, 58569, 36903): b"parent-osm"})
    service = MapTileService(tmp_path)
    assert service.tile(17, 117138, 73806) == b"parent-osm"
    assert service.tile(17, 117139, 73807) == b"parent-osm"
    # The light theme must not be served a dark parent.
    rendered = service.tile(17, 117138, 73806, light=True)
    assert rendered != b"parent-osm"
    assert rendered[:8] == b"\x89PNG\r\n\x1a\n"


def test_an_uncovered_area_falls_back_to_the_procedural_reference(tmp_path: Path) -> None:
    """Outside the pack the surface is generated, and the tile still has to come back —
    a mission flown elsewhere must not show a grey hole."""
    _write_pack(tmp_path, [15], {(15, 29284, 18451): b"covered"})
    service = MapTileService(tmp_path)
    data = service.tile(15, 1, 1)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data != b"covered"
    assert (tmp_path / "15" / "1" / "1.png").exists()


def test_zoom_is_clamped_to_the_supported_range(tmp_path: Path) -> None:
    service = MapTileService(tmp_path)
    low = service.tile(4, 3, 3)
    assert low == service.tile(MIN_ZOOM, 3, 3)
    assert service.tile(20, 3, 3) == service.tile(MAX_ZOOM, 3, 3)


# ------------------------------------------------------------------ build pipeline
def test_zoom_range_parsing_clamps_to_the_supported_band() -> None:
    assert offline_map._zoom_range("10-13") == [10, 11, 12, 13]
    assert offline_map._zoom_range("12,14,16") == [12, 14, 16]
    assert offline_map._zoom_range("6-30") == list(range(MIN_ZOOM, MAX_ZOOM + 1))


def test_every_tag_bucket_maps_to_a_drawn_layer() -> None:
    """A typo in a bucket name drops the feature silently: the renderer only walks the
    buckets it knows, so an unmapped colour name means roads that exist in the data and
    never appear on the operator's screen."""
    drawn = set(basemap_tool.POLY_STYLE) | set(basemap_tool.LINE_STYLE) | {"building"}
    assert set(offline_map.ROAD_BUCKETS.values()) <= drawn
    assert set(offline_map.AREA_BUCKETS.values()) <= drawn


def test_line_features_are_not_routed_into_a_polygon_bucket() -> None:
    """`build` sends non-polygon area features to `waterway` and counts the rest in
    `skipped`; putting a LineString into `residential` would raise at build time."""
    drawn_poly = set(basemap_tool.POLY_STYLE)
    for name, bucket in offline_map.AREA_BUCKETS.items():
        if bucket not in drawn_poly:
            assert bucket == "waterway", f"{name} routes to an undrawn bucket"


def test_the_pack_on_disk_matches_the_documented_coverage() -> None:
    """The repository ships the recipe, not the 1.6 GB ingredient — but if a pack is
    present it must be self-describing, or the UI is labelling a surface it cannot prove."""
    manifest_path = REPO / "data" / "map_tiles" / "map_manifest.json"
    if not manifest_path.exists():
        pytest.skip("no tile pack installed; the procedural fallback is the documented state")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"]["osm_data_date"]
    assert manifest["coverage"]["zooms"] == sorted(manifest["coverage"]["zooms"])
    assert manifest["simplifications"], "a rendered pack must publish what it left out"
    service = MapTileService(REPO / "data" / "map_tiles")
    assert service.provenance()["kind"] == "openstreetmap-offline-pack"


# ------------------------------------------------------------------------ the route
def test_manifest_route_publishes_provenance_without_a_network_call() -> None:
    from fastapi.testclient import TestClient
    from rescueswarm.main import app

    with TestClient(app) as client:
        response = client.get("/api/map/manifest")
        assert response.status_code == 200
        body = response.json()
        assert body["network_access"] is False
        assert body["kind"] in {"procedural", "openstreetmap-offline-pack"}
        assert isinstance(body["attribution"], list) and body["attribution"]
        style = client.get("/api/map/style").json()
        assert style["metadata"]["rescue-swarm:basemap"] == body["kind"]
