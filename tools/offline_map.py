#!/usr/bin/env python
"""Build the offline OpenStreetMap basemap that RescueSwarm serves without a network.

    python tools/offline_map.py extract                    # .pbf -> parquet (long, one pass per layer)
    python tools/offline_map.py build  --zooms 10-16       # parquet -> tiles + manifest
    python tools/offline_map.py status                     # what is on disk right now

Two stages because the first is a several-minute scan of a multi-gigabyte extract and
the second is a fast render that gets retuned repeatedly. Splitting them means a colour
change never costs another file scan.

The tiles land in the same directory MapTileService already reads
(`data/map_tiles/{z}/{x}/{y}[-light].png`), so the running GCS needs no API change to
serve them; the manifest beside them is what tells the operator which part of the
picture is OpenStreetMap and which is procedural fallback.

The .pbf itself is never committed — it is 1.6 GB of third-party data and the repo ships
the recipe, not the ingredient.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

DEFAULT_PBF = ROOT / "map" / "india-261030.osm.pbf"
EXTRACT_DIR = ROOT / "map" / "extract"
TILE_DIR = ROOT / "data" / "map_tiles"

# (west, south, east, north) in degrees — the order shapely and pyrosm 0.28 both accept.
# METRO is wide enough that zooming out over Bengaluru still shows real lakes and arterials;
# DEMO is the box the sample mission actually flies in (pad 12.9487/77.5991, sample AOI
# 77.5977-77.6023 / 12.9491-12.9509) with room for RTL and a diversion.
METRO = (77.40, 12.75, 77.90, 13.25)
DEMO = (77.540, 12.895, 77.660, 13.005)
METRO_MAX_ZOOM = 12

ROAD_BUCKETS = {
    "motorway": "road_major", "motorway_link": "road_major", "trunk": "road_major",
    "trunk_link": "road_major", "primary": "road_major", "primary_link": "road_major",
    "secondary": "road_medium", "secondary_link": "road_medium",
    "tertiary": "road_minor", "tertiary_link": "road_minor", "unclassified": "road_minor",
    "residential": "road_minor", "living_street": "road_minor", "road": "road_minor",
    "service": "road_path", "footway": "road_path", "path": "road_path",
    "pedestrian": "road_path", "cycleway": "road_path", "track": "road_path",
    "steps": "road_path", "bridleway": "road_path",
}

AREA_BUCKETS = {
    "water": "water", "riverbank": "water", "basin": "water",
    "river": "waterway", "stream": "waterway", "canal": "waterway",
    "drain": "waterway", "ditch": "waterway",
    "wood": "wood", "forest": "wood",
    "wetland": "wetland", "marsh": "wetland",
    "residential": "residential", "redevelopment": "residential",
    "commercial": "commercial", "retail": "commercial",
    "industrial": "industrial", "warehouse": "industrial", "railway": "industrial",
    "quarry": "quarry", "brownfield": "quarry", "landfill": "quarry",
    "farmland": "farmland", "farmyard": "farmland", "fallow": "farmland",
    "meadow": "green", "grass": "green", "orchard": "green", "vineyard": "green",
    "plant_nursery": "green", "allotments": "green", "scrub": "green", "grassland": "green",
    "park": "green", "garden": "green", "recreation_ground": "green",
    "playground": "green", "nature_reserve": "green",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _polygon(bbox: tuple[float, float, float, float]):
    from shapely.geometry import box

    return box(*bbox)


def _log(message: str) -> None:
    print(message, flush=True)


# ------------------------------------------------------------------------ extract
def _pbf_headers(pbf: Path) -> dict[str, Any]:
    """Provenance straight out of the .pbf header block, not out of the filename.

    pyrosm 0.28 no longer exports `get_headers`, so pyosmium's reader header is used;
    the replication timestamp is what makes "the map is stale by N days" an honest
    statement in the manifest instead of a guess.
    """
    import osmium.io

    reader = osmium.io.Reader(str(pbf))
    try:
        header = reader.header()
        box = header.box()
        return {
            "osm_data_date": header.get("osmosis_replication_timestamp"),
            "replication_sequence": header.get("osmosis_replication_sequence_number"),
            "generator": header.get("generator"),
            "file_bbox": None if box is None else str(box),
        }
    finally:
        reader.close()


def cmd_extract(args: argparse.Namespace) -> int:
    import pyrosm
    from pyrosm import OSM

    pbf = Path(args.pbf)
    if not pbf.exists():
        _log(f"ERROR  no OSM extract at {pbf}")
        _log("       Place a regional .osm.pbf (Geofabrik) there and re-run.")
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    headers = _pbf_headers(pbf)
    _log(f"source {pbf.name} · {pbf.stat().st_size / 2**30:.2f} GiB · pyrosm {pyrosm.__version__}")
    _log(f"       file bbox {headers.get('file_bbox')} · generator {headers.get('generator')}")
    _log(f"       OSM data timestamp {headers.get('osm_data_date')}")

    jobs = [
        ("network", OSM(str(pbf), bounding_box=_polygon(METRO)), "get_network"),
        ("landuse", OSM(str(pbf), bounding_box=_polygon(METRO)), "get_landuse"),
        ("natural", OSM(str(pbf), bounding_box=_polygon(METRO)), "get_natural"),
        ("boundaries", OSM(str(pbf), bounding_box=_polygon(METRO)), "get_boundaries"),
        ("buildings", OSM(str(pbf), bounding_box=_polygon(DEMO)), "get_buildings"),
    ]

    recorded: dict[str, Any] = {
        "stage": "extract",
        "built_at": _now(),
        "pyrosm_version": pyrosm.__version__,
        "source_file": pbf.name,
        "source_bytes": pbf.stat().st_size,
        "source_mtime": datetime.fromtimestamp(pbf.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "file_bbox": headers.get("file_bbox"),
        "generator": headers.get("generator"),
        "osm_data_date": headers.get("osm_data_date"),
        "replication_sequence": headers.get("replication_sequence"),
        "boxes": {"metro": list(METRO), "demo": list(DEMO)},
        "layers": {},
    }

    for name, osm, method in jobs:
        t0 = time.time()
        try:
            frame = getattr(osm, method)() if method != "get_network" else osm.get_network(
                network_type="driving", extra_attributes=["highway", "bridge", "tunnel", "name"]
            )
        except Exception as exc:  # noqa: BLE001 - a missing layer must not kill the build
            _log(f"layer  {name:<11} FAILED {type(exc).__name__}: {exc}")
            recorded["layers"][name] = {"status": "failed", "error": str(exc)}
            continue
        if frame is None or len(frame) == 0:
            _log(f"layer  {name:<11} empty ({time.time() - t0:.0f}s) — recorded as absent")
            recorded["layers"][name] = {"status": "empty", "rows": 0, "seconds": round(time.time() - t0, 1)}
            continue
        path = out / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        info = {
            "status": "written",
            "rows": int(len(frame)),
            "file": path.name,
            "bytes": path.stat().st_size,
            "seconds": round(time.time() - t0, 1),
            "geometry_types": sorted({g.geom_type for g in frame.geometry}),
        }
        for column in ("highway", "landuse", "natural", "leisure", "amenity", "building", "waterway"):
            if column in frame.columns:
                top = frame[column].value_counts().head(12)
                info[f"{column}_values"] = {str(k): int(v) for k, v in top.items()}
        recorded["layers"][name] = info
        _log(f"layer  {name:<11} {info['rows']:>8} rows · {info['bytes'] / 2**20:6.1f} MiB · {info['seconds']:>5.1f}s")
        for column in ("highway", "landuse", "natural", "leisure", "waterway"):
            values = info.get(f"{column}_values")
            if values:
                _log(f"        {column}: " + ", ".join(f"{k}×{v}" for k, v in list(values.items())[:12]))

    (out / "extract_meta.json").write_text(json.dumps(recorded, indent=2), encoding="utf-8")
    present = [k for k, v in recorded["layers"].items() if v.get("status") == "written"]
    _log(f"done   {', '.join(present) or 'nothing'} -> {out}")
    return 0 if present else 1


# -------------------------------------------------------------------------- build
def _rings(geom, kind: str) -> Iterable[list[tuple[float, float]]]:
    """Exterior rings / linestrings as (lon, lat) tuples. Holes are documented as skipped."""
    if geom is None or geom.is_empty:
        return []
    if kind == "poly":
        parts = geom.geoms if geom.geom_type.startswith("Multi") else [geom]
        return [list(part.exterior.coords) for part in parts if part.geom_type == "Polygon"]
    parts = geom.geoms if geom.geom_type.startswith("Multi") else [geom]
    return [list(p.coords) for p in parts if p.geom_type == "LineString"]


def _read(out: Path, name: str):
    """Load a cached layer as a GeoDataFrame.

    `pandas.read_parquet` would hand back raw WKB bytes in the geometry column and every
    render call would fail on `geom.geom_type`; only geopandas decodes it to shapely.
    """
    import geopandas as gpd

    path = out / f"{name}.parquet"
    if not path.exists():
        return None
    return gpd.read_parquet(path)


def cmd_build(args: argparse.Namespace) -> int:
    from osm_basemap import BUILDING_MIN_ZOOM, Basemap, attribution_lines, render_tile, tiles_for_bounds

    src = Path(args.out)
    # The intermediate geometry lives beside the source data, not inside the tile cache:
    # everything under `data/map_tiles` is meant to be either a served tile or the manifest.
    basemap_dir = ROOT / "map" / "build" / "basemap"
    stale = src / "basemap"
    if stale.exists():
        shutil.rmtree(stale, ignore_errors=True)
    zooms = _zoom_range(args.zooms)
    t0 = time.time()
    base = Basemap(region="bengaluru")
    base.meta = {
        "extract_meta_file": str(EXTRACT_DIR / "extract_meta.json"),
        "coverage_deg": {"metro": list(METRO), "demo": list(DEMO)},
        "zooms": zooms,
        "building_min_zoom": BUILDING_MIN_ZOOM,
        "simplifications": [
            "polygon interior rings (lake islands, courtyards) are filled, not punched",
            "no street or place labels",
            "geometry culled by bounding box, not clipped to tile edges",
        ],
    }

    network = _read(EXTRACT_DIR, "network")
    if network is not None:
        counts = {"road_major": 0, "road_medium": 0, "road_minor": 0, "road_path": 0}
        for klass, line in zip(network["highway"], network["geometry"]):
            bucket = ROAD_BUCKETS.get(str(klass))
            if not bucket:
                continue
            rings = list(_rings(line, "line"))
            if rings:
                base.add_lines(bucket, rings)
                counts[bucket] += len(rings)
        base.meta["road_classes"] = counts
        _log(f"roads  " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    buildings = _read(EXTRACT_DIR, "buildings")
    if buildings is not None:
        rings = [r for geom in buildings["geometry"] for r in _rings(geom, "poly")]
        base.add_polygons("building", rings)
        _log(f"buildings {len(rings)}")

    for source, label in (("landuse", "landuse"), ("natural", "natural")):
        frame = _read(EXTRACT_DIR, source)
        if frame is None:
            continue
        tag_columns = [c for c in ("waterway", "landuse", "natural", "leisure", "amenity")
                       if c in frame.columns]
        buckets: dict[str, int] = {}
        skipped: dict[str, int] = {}
        for row in frame.itertuples(index=False):
            geom = getattr(row, "geometry")
            polygon = geom.geom_type in ("Polygon", "MultiPolygon")
            chosen = None
            for column in tag_columns:
                value = str(getattr(row, column, None))
                if value in AREA_BUCKETS:
                    chosen = AREA_BUCKETS[value]
                    break
            if chosen is None:
                continue
            rings = list(_rings(geom, "poly" if polygon else "line"))
            if not rings:
                continue
            if not polygon and chosen == "water":
                # A river is a line feature, and it needs its own bucket so it can be
                # drawn thin instead of filling the tile with water colour.
                chosen = "waterway"
            if not polygon and chosen != "waterway":
                # A line tagged as an area class (a hedge-length "grass", a footpath
                # tagged "residential") has no honest representation in a fill bucket.
                skipped["line_not_area"] = skipped.get("line_not_area", 0) + len(rings)
                continue
            if polygon:
                base.add_polygons(chosen, rings)
            else:
                base.add_lines(chosen, rings)
            buckets[chosen] = buckets.get(chosen, 0) + len(rings)
        base.meta[f"{label}_buckets"] = buckets
        base.meta[f"{label}_skipped"] = skipped
        _log(f"{label:<9} " + (", ".join(f"{k}={v}" for k, v in sorted(buckets.items())) or "none mapped"))

    # `boundaries` is extracted for provenance but deliberately not drawn: an
    # administrative outline is not terrain, and filling it would blanket the map.
    base.meta["layers_not_drawn"] = ["boundaries (administrative outlines are not a tactical layer)"]

    base.finalize()
    base.save(basemap_dir)
    _log(f"basemap {sum(base.feature_counts().values()):,} features -> {basemap_dir}")

    # Clear cached tiles for the zooms being rebuilt: a stale procedural PNG under the
    # same z/x/y would otherwise survive the new pack and read as real OSM.
    removed = 0
    for z in zooms:
        folder = src / str(z)
        if folder.exists():
            removed += sum(1 for p in folder.rglob("*.png"))
            shutil.rmtree(folder, ignore_errors=True)
    if removed:
        _log(f"cleared {removed} previously cached tile(s) at zoom {zooms}")

    tile_count = 0
    written_boxes: dict[str, list[float]] = {}
    for z in zooms:
        # Low zoom rasterises the wide metro box; high zoom the demo box. The whole city
        # at z16 would be eleven million tiles for a view the operator never flies.
        bbox = METRO if z <= METRO_MAX_ZOOM else DEMO
        tiles = tiles_for_bounds(*bbox, z)
        t1 = time.time()
        for (x, y) in tiles:
            for suffix, theme in (("", "dark"), ("-light", "light")):
                path = src / str(z) / str(x) / f"{y}{suffix}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                render_tile(base, z, x, y, theme).save(path, optimize=True)
                tile_count += 1
        written_boxes[str(z)] = list(bbox)
        _log(f"z{z:<3} {len(tiles):>5} tiles · {'metro' if z <= METRO_MAX_ZOOM else 'demo '} box · {time.time() - t1:.1f}s")

    manifest = {
        "format": "rescueswarm-offline-basemap-v1",
        "built_at": _now(),
        "elapsed_s": round(time.time() - t0, 1),
        "source": _source_record(),
        "coverage": {
            "zoom_boxes_deg": written_boxes,
            "metro_bbox_deg": list(METRO),
            "demo_bbox_deg": list(DEMO),
            "zooms": zooms,
            "tiles_written": tile_count,
        },
        "layers": base.feature_counts(),
        "simplifications": base.meta["simplifications"],
        "notes": [
            "Tiles inside the covered boxes are rendered from OpenStreetMap data.",
            "Outside them the GCS falls back to its procedural reference surface and says so.",
            "No network access happens at run time.",
        ],
    }
    manifest["attribution"] = attribution_lines(manifest)
    (src / "map_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    size = sum(p.stat().st_size for p in src.rglob("*.png"))
    _log(f"done   {tile_count} tiles · {size / 2**20:.1f} MiB · manifest {src / 'map_manifest.json'}")
    return 0


def _source_record() -> dict[str, Any]:
    path = EXTRACT_DIR / "extract_meta.json"
    if not path.exists():
        return {"status": "unknown", "note": "run `offline_map.py extract` first"}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "provider": "OpenStreetMap (© OpenStreetMap contributors, ODbL)",
        "extract_file": raw.get("source_file"),
        "extract_bytes": raw.get("source_bytes"),
        "osm_data_date": raw.get("osm_data_date"),
        "replication_sequence": raw.get("replication_sequence"),
        "generator": raw.get("generator"),
        "file_bbox": raw.get("file_bbox"),
        "extracted_at": raw.get("built_at"),
        "tool": f"pyrosm {raw.get('pyrosm_version')}",
        "layers": {k: v.get("status") for k, v in (raw.get("layers") or {}).items()},
    }


def _zoom_range(text: str) -> list[int]:
    if "-" in text:
        low, high = text.split("-", 1)
        return [z for z in range(int(low), int(high) + 1) if 10 <= z <= 17]
    return [z for z in (int(v) for v in text.split(",")) if 10 <= z <= 17]


# ------------------------------------------------------------------------- status
def cmd_status(args: argparse.Namespace) -> int:  # noqa: ARG001
    manifest_path = TILE_DIR / "map_manifest.json"
    if not manifest_path.exists():
        _log(f"no offline basemap manifest at {manifest_path}")
        _log("the GCS is serving its procedural reference surface")
        return 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _log(json.dumps(manifest, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract", help="read the .pbf and cache the layers as parquet")
    extract.add_argument("--pbf", default=str(DEFAULT_PBF))
    extract.add_argument("--out", default=str(EXTRACT_DIR))
    extract.set_defaults(func=cmd_extract)

    build = sub.add_parser("build", help="render tiles from the cached layers")
    build.add_argument("--out", default=str(TILE_DIR))
    build.add_argument("--zooms", default="10-16")
    build.set_defaults(func=cmd_build)

    status = sub.add_parser("status", help="show the manifest of the installed tile pack")
    status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
