"""Offline basemap geometry and raster tile renderer built from OpenStreetMap data.

This is the build-time half of the offline map. It takes the few layers a tactical GCS
actually needs from a regional OSM extract (water, land cover, roads, buildings), keeps
them as merged float32 coordinates in the slippy-tile projection, and rasterises them
into the same on-disk cache that `rescueswarm.map_tiles.MapTileService` already serves —
so the operator sees real streets and real lakes instead of the procedural reference
surface, still with zero network access at run time.

Why merged arrays instead of GeoDataFrames: one tile is then a single vectorised
bounding-box mask plus a handful of PIL draw calls, which turns a few thousand tiles
into a minutes-long job rather than an hours-long one.

Known, deliberate simplifications (recorded in the manifest the app displays):
  * Polygon interior rings (holes, lake islands) are not punched out. A water body with
    an island renders as solid water.
  * No labels are drawn. Text on a tactical basemap competes with survivor markers,
    and the extract's `name` coverage is patchy enough that a partial label set would
    read as a broken map.
  * Geometry is not clipped to the tile; features are culled by bounding box only, so a
    long road is submitted to PIL in full and clipped by the rasteriser.

Licensing: OpenStreetMap data is © OpenStreetMap contributors under the ODbL. A build
records its source in map_manifest.json and the application attributes it on screen.
Nothing here invents features: if the extract has no building, the tile has no building.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw

TILE_SIZE = 256
EARTH_RADIUS = 6_378_137.0
SEMICYCLE = math.pi * EARTH_RADIUS
ORIGIN = -SEMICYCLE

FORMAT = "rescueswarm-basemap-v2"


# --------------------------------------------------------------------- projection
def lnglat_to_mercator(lon: float, lat: float) -> tuple[float, float]:
    """WGS84 degrees to EPSG:3857 metres."""
    x = math.radians(lon) * EARTH_RADIUS
    sin_lat = max(min(math.sin(math.radians(lat)), 0.9999999), -0.9999999)
    y = EARTH_RADIUS * math.log((1 + sin_lat) / (1 - sin_lat)) / 2
    return x, y


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) in degrees for one slippy tile."""
    n = 2**z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0

    def lat(py: int) -> float:
        value = math.pi - 2.0 * math.pi * py / n
        return math.degrees(math.atan(math.sinh(value)))

    return west, lat(y + 1), east, lat(y)


def tiles_for_bounds(west: float, south: float, east: float, north: float, z: int) -> list[tuple[int, int]]:
    """Every tile index that intersects a degree box at zoom `z`."""
    n = 2**z

    def tile_x(lon: float) -> int:
        return int(math.floor((lon + 180.0) / 360.0 * n))

    def tile_y(lat: float) -> int:
        rad = math.radians(lat)
        return int(math.floor((1.0 - math.log(math.tan(rad) + 1 / math.cos(rad)) / math.pi) / 2 * n))

    x0, x1 = tile_x(west), tile_x(east)
    # north is the *top* of the box, so it has the smaller y index
    y0, y1 = tile_y(north), tile_y(south)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


# ----------------------------------------------------------------------- geometry
@dataclass
class Layer:
    """One style bucket: merged projected coordinates plus per-feature index arrays.

    `coords` is float32 [x0, y0, x1, y1, ...]; feature *i* occupies pairs
    `coords[start[i] : start[i] + count[i]]`. `bbox` is float32 (n, 4) of
    (min_x, min_y, max_x, max_y) in metres, which is what makes tile cilling a single
    numpy comparison instead of a Python loop over every building in the region.
    """

    kind: str  # "poly" | "line" | "point"
    coords: list[float] = field(default_factory=list)
    start: list[int] = field(default_factory=list)
    count: list[int] = field(default_factory=list)
    bbox: list[tuple[float, float, float, float]] = field(default_factory=list)
    _xy: np.ndarray | None = None
    _start: np.ndarray | None = None
    _count: np.ndarray | None = None
    _bbox: np.ndarray | None = None

    def add(self, ring: Sequence[tuple[float, float]]) -> None:
        if self._xy is not None:
            raise RuntimeError("layer was finalised for rendering; build every ring first")
        minimum = 3 if self.kind == "poly" else 2
        if len(ring) < minimum:
            return
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        self.start.append(len(self.coords) // 2)
        self.count.append(len(ring))
        self.coords.extend(v for pair in zip(xs, ys) for v in pair)
        self.bbox.append((min(xs), min(ys), max(xs), max(ys)))

    def arrays(self) -> None:
        """Swap the Python accumulators for the numpy views the renderer uses."""
        if self._xy is not None:
            return
        self._xy = np.asarray(self.coords, dtype=np.float32).reshape(-1, 2)
        self._start = np.asarray(self.start, dtype=np.int64)
        self._count = np.asarray(self.count, dtype=np.int64)
        self._bbox = np.asarray(self.bbox, dtype=np.float32).reshape(-1, 4)
        # A million building rings held twice (Python lists plus float32) is the difference
        # between a build that finishes and one that pages out.
        self.coords = []
        self.bbox = []

    def feature_count(self) -> int:
        if self._start is not None:
            return int(self._start.shape[0])
        return len(self.start)


@dataclass
class Basemap:
    """Every renderable layer for one region, keyed by style-bucket name."""

    region: str
    layers: dict[str, Layer] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    ready: bool = False

    def layer(self, bucket: str, kind: str) -> Layer:
        existing = self.layers.get(bucket)
        if existing is not None and existing.kind != kind:
            raise ValueError(f"bucket {bucket!r} already holds {existing.kind!r}, not {kind!r}")
        if existing is None:
            existing = Layer(kind=kind)
            self.layers[bucket] = existing
        return existing

    def add_polygons(self, bucket: str, rings: Iterable[Sequence[tuple[float, float]]]) -> int:
        return self._ingest(bucket, "poly", rings)

    def add_lines(self, bucket: str, lines: Iterable[Sequence[tuple[float, float]]]) -> int:
        return self._ingest(bucket, "line", lines)

    def _ingest(self, bucket: str, kind: str, rings: Iterable[Sequence[tuple[float, float]]]) -> int:
        """Project WGS84 rings once, at build time, so rendering never touches trigonometry."""
        layer = self.layer(bucket, kind)
        added = 0
        for ring in rings:
            projected = [lnglat_to_mercator(lon, lat) for lon, lat in ring]
            if kind == "poly" and projected and projected[0] != projected[-1]:
                projected.append(projected[0])
            layer.add(projected)
            added += 1
        return added

    def finalize(self) -> "Basemap":
        for layer in self.layers.values():
            layer.arrays()
        self.ready = True
        return self

    # ------------------------------------------------------------------- io
    def save(self, directory: Path) -> None:
        if not self.ready:
            self.finalize()
        directory.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {}
        for name, layer in sorted(self.layers.items()):
            payload[f"{name}__kind"] = np.asarray([layer.kind])  # stored as a 1-element array
            payload[f"{name}__xy"] = layer._xy
            payload[f"{name}__start"] = layer._start
            payload[f"{name}__count"] = layer._count
            payload[f"{name}__bbox"] = layer._bbox
        np.savez_compressed(directory / "basemap.npz", **payload)
        (directory / "basemap_meta.json").write_text(
            json.dumps({"format": FORMAT, "region": self.region, "meta": self.meta,
                        "layers": {n: l.feature_count() for n, l in sorted(self.layers.items())}},
                       indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, directory: Path) -> "Basemap":
        meta = json.loads((directory / "basemap_meta.json").read_text(encoding="utf-8"))
        if meta.get("format") != FORMAT:
            raise ValueError(f"{directory} was built by a different basemap format ({meta.get('format')!r})")
        base = cls(region=str(meta.get("region", "")), meta=dict(meta.get("meta", {})))
        with np.load(directory / "basemap.npz") as raw:
            names = sorted({key.split("__", 1)[0] for key in raw.files})
            for name in names:
                kind = str(raw[f"{name}__kind"][0])
                layer = Layer(kind=kind)
                layer._xy = raw[f"{name}__xy"]
                layer._start = raw[f"{name}__start"]
                layer._count = raw[f"{name}__count"]
                layer._bbox = raw[f"{name}__bbox"]
                base.layers[name] = layer
        base.ready = True
        return base

    def feature_counts(self) -> dict[str, int]:
        return {name: layer.feature_count() for name, layer in sorted(self.layers.items())}


# -------------------------------------------------------------------------- style
# Two themes, because MapTileService serves a "-light" variant beside the default.
# Colours are tuned to sit under the tactical overlays without competing with them:
# the map must never out-shout a survivor marker or a fence line.
DARK = {
    "background": (13, 19, 27),
    "water": (17, 36, 54),
    "waterway": (24, 48, 68),
    "wetland": (16, 40, 36),
    "wood": (18, 34, 26),
    "green": (20, 38, 30),
    "farmland": (21, 29, 25),
    "residential": (20, 27, 36),
    "commercial": (24, 29, 38),
    "industrial": (27, 30, 37),
    "quarry": (25, 28, 33),
    "building": (33, 45, 58),
    "road_minor": (46, 62, 80),
    "road_medium": (64, 84, 110),
    "road_major": (88, 114, 152),
    "road_path": (38, 50, 64),
    "casing": (10, 14, 20),
}
LIGHT = {
    "background": (232, 236, 241),
    "water": (175, 205, 228),
    "waterway": (158, 193, 220),
    "wetland": (187, 214, 197),
    "wood": (197, 220, 197),
    "green": (204, 225, 203),
    "farmland": (224, 227, 214),
    "residential": (223, 226, 231),
    "commercial": (227, 221, 226),
    "industrial": (220, 217, 223),
    "quarry": (214, 214, 218),
    "building": (205, 210, 218),
    "road_minor": (200, 205, 212),
    "road_medium": (178, 184, 195),
    "road_major": (152, 161, 178),
    "road_path": (208, 212, 217),
    "casing": (242, 244, 247),
}

# bucket -> (paint key, first zoom it appears, width in px at that zoom)
POLY_ORDER = ("residential", "commercial", "industrial", "quarry", "farmland", "wood",
              "wetland", "green", "water")
POLY_STYLE: dict[str, tuple[str, int]] = {
    "residential": ("residential", 11),
    "commercial": ("commercial", 11),
    "industrial": ("industrial", 11),
    "quarry": ("quarry", 11),
    "farmland": ("farmland", 11),
    "wood": ("wood", 10),
    "wetland": ("wetland", 11),
    "green": ("green", 11),
    "water": ("water", 10),
}

LINE_ORDER = ("waterway", "road_major", "road_medium", "road_minor", "road_path")
LINE_STYLE: dict[str, tuple[str, int, float]] = {
    "waterway": ("waterway", 12, 0.8),
    "road_major": ("road_major", 10, 1.5),
    "road_medium": ("road_medium", 12, 1.2),
    "road_minor": ("road_minor", 13, 0.9),
    "road_path": ("road_path", 15, 0.7),
}
BUILDING_MIN_ZOOM = 14


def _width(zoom: int, base_zoom: int, base_width: float) -> int:
    """Road width grows with zoom, capped so z16 does not turn into ribbons.

    Pillow's line rasteriser takes an integer stroke, so the width is rounded here rather
    than at the draw call. The growth factor is deliberately gentle: the first build used
    1.55 and every residential lane came out as a 5 px casing that swallowed the block
    pattern the operator is actually searching.
    """
    return int(round(min(7.0, max(0.7, base_width * (1.35 ** max(0, zoom - base_zoom))))))


# ------------------------------------------------------------------------ drawing
def render_tile(base: Basemap, z: int, x: int, y: int, theme: str = "dark") -> Image.Image:
    """Rasterise one basemap tile. Pure function of (base, z, x, y, theme)."""
    if not base.ready:
        base.finalize()
    palette = DARK if theme == "dark" else LIGHT
    grid = 2**z
    span = (2 * SEMICYCLE) / grid        # metres per tile
    res = span / TILE_SIZE               # metres per pixel
    west = ORIGIN + x * span
    south = ORIGIN + (grid - 1 - y) * span

    image = Image.new("RGB", (TILE_SIZE, TILE_SIZE), palette["background"])
    draw = ImageDraw.Draw(image, "RGBA")
    north = south + span
    east = west + span

    def visible_features(layer: Layer) -> list[np.ndarray]:
        box = layer._bbox
        hits = np.where((box[:, 0] <= east) & (box[:, 2] >= west)
                        & (box[:, 1] <= north) & (box[:, 3] >= south))[0]
        return [layer._xy[s:s + c] for s, c in zip(layer._start[hits].tolist(),
                                                   layer._count[hits].tolist())]

    def as_pixels(points: np.ndarray) -> list[tuple[float, float]]:
        px = (points[:, 0] - west) / res
        py = (north - points[:, 1]) / res
        return list(zip(px.tolist(), py.tolist()))

    for bucket in POLY_ORDER:
        layer = base.layers.get(bucket)
        if layer is None:
            continue
        paint, min_zoom = POLY_STYLE[bucket]
        if z < min_zoom:
            continue
        for ring in visible_features(layer):
            if len(ring) < 3:
                continue
            draw.polygon(as_pixels(ring), fill=palette[paint])

    if z >= BUILDING_MIN_ZOOM:
        layer = base.layers.get("building")
        if layer is not None:
            for ring in visible_features(layer):
                if len(ring) < 3:
                    continue
                draw.polygon(as_pixels(ring), fill=palette["building"])

    # Casings first, so a major road reads as a road and not as a bright line floating
    # over the smaller ones it crosses. Pillow has no batched polyline call, so each
    # feature is one draw — which is why culling by bbox matters more than anything else
    # in this loop.
    for bucket in LINE_ORDER:
        layer = base.layers.get(bucket)
        if layer is None:
            continue
        _, min_zoom, base_width = LINE_STYLE[bucket]
        if z < min_zoom:
            continue
        width = _width(z, min_zoom, base_width)
        if width < 4:
            continue
        for line in visible_features(layer):
            if len(line) < 2:
                continue
            draw.line(as_pixels(line), fill=palette["casing"], width=width + 2, joint="curve")
    for bucket in LINE_ORDER:
        layer = base.layers.get(bucket)
        if layer is None:
            continue
        style_key, min_zoom, base_width = LINE_STYLE[bucket]
        if z < min_zoom:
            continue
        width = _width(z, min_zoom, base_width)
        for line in visible_features(layer):
            if len(line) < 2:
                continue
            draw.line(as_pixels(line), fill=palette[style_key], width=width, joint="curve")

    return image


def attribution_lines(manifest: dict[str, Any] | None) -> list[str]:
    """Human-readable provenance for the UI, built from what a build actually recorded."""
    if not manifest:
        return ["Procedural offline reference · not survey data"]
    source = manifest.get("source") or {}
    data_date = source.get("osm_data_date") or source.get("extract_date") or "date unknown"
    layers = ", ".join(sorted((manifest.get("layers") or {}).keys())) or "none"
    return [
        f"OpenStreetMap © OpenStreetMap contributors · ODbL · data as of {data_date}",
        f"Offline tile pack ({layers}) · built {manifest.get('built_at', 'unknown')} · "
        f"outside the covered area the surface falls back to procedural reference",
    ]
