"""Offline tactical basemap tiles, with two distinct sources that are never confused.

The map never contacts an online tile service. Tiles come from one of two places:

1. an OpenStreetMap tile pack rendered by `tools/offline_map.py` and recorded in
   `data/map_tiles/map_manifest.json` — real streets, lakes and buildings, read from a
   regional .pbf at build time;
2. the procedural reference surface generated below from a deterministic terrain field,
   which fills every tile the pack does not cover.

Which one the operator is looking at is published by `provenance()` and carried in the
style attribution, because "the map is wrong" and "the map is honest but coarse" are very
different problems. The mission geometry on top is exact either way.
"""

from __future__ import annotations

import io
import json
import math
import threading
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .config import data_dir

TILE_SIZE = 256
MIN_ZOOM = 10
MAX_ZOOM = 17
MANIFEST = "map_manifest.json"
PROCEDURAL_ATTRIBUTION = "Procedural offline reference · not satellite imagery"


def _tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2 ** z

    def lon(px: float) -> float:
        return px / n * 360.0 - 180.0

    def lat(py: float) -> float:
        value = math.pi - 2.0 * math.pi * py / n
        return math.degrees(math.atan(math.sinh(value)))

    return lon(x), lat(y + 1), lon(x + 1), lat(y)


class MapTileService:
    def __init__(self, root: Path | None = None) -> None:
        self.cache_dir = (root or data_dir() / "map_tiles")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory: dict[tuple[int, int, int], bytes] = {}
        self._lock = threading.RLock()
        self._manifest: dict[str, Any] | None = None
        self._manifest_stamp: tuple[float, int] | None = None

    # ------------------------------------------------------------------ manifest
    def manifest(self) -> dict[str, Any] | None:
        """The tile pack's own record of where it came from, re-read if it changed.

        A rebuild while the service runs should not need a restart to be reported
        honestly, so the check is the file's mtime and size rather than a flag.
        """
        path = self.cache_dir / MANIFEST
        try:
            stat = path.stat()
            stamp = (stat.st_mtime, stat.st_size)
        except OSError:
            with self._lock:
                self._manifest = None
                self._manifest_stamp = None
            return None
        with self._lock:
            if self._manifest is not None and self._manifest_stamp == stamp:
                return self._manifest
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A half-written manifest must not take the map down; treat it as no pack.
            raw = None
        with self._lock:
            self._manifest = raw if isinstance(raw, dict) else None
            self._manifest_stamp = stamp
        return self._manifest

    def pack_max_zoom(self) -> int | None:
        manifest = self.manifest()
        if not manifest:
            return None
        zooms = ((manifest.get("coverage") or {}).get("zooms")) or []
        numbers = [int(z) for z in zooms if str(z).lstrip("-").isdigit()]
        return max(numbers) if numbers else None

    def provenance(self) -> dict[str, Any]:
        """What the operator is actually looking at, for the style and the UI badge."""
        manifest = self.manifest()
        if not manifest:
            return {
                "kind": "procedural",
                "label": "Procedural reference surface",
                "attribution": [PROCEDURAL_ATTRIBUTION],
                "network_access": False,
                "pack_zooms": [],
                "coverage_deg": None,
                "osm_data_date": None,
                "note": ("No OpenStreetMap tile pack is installed. Run "
                         "`python tools/offline_map.py extract` then `build` to render one."),
            }
        coverage = manifest.get("coverage") or {}
        boxes = coverage.get("zoom_boxes_deg") or {}
        metro = coverage.get("metro_bbox_deg")
        return {
            "kind": "openstreetmap-offline-pack",
            "label": "OpenStreetMap offline tile pack",
            "attribution": list(manifest.get("attribution") or []),
            "network_access": False,
            "pack_zooms": sorted(int(z) for z in coverage.get("zooms") or []),
            "coverage_deg": metro or (min((v[0] for v in boxes.values()), default=None),
                                      min((v[1] for v in boxes.values()), default=None),
                                      max((v[2] for v in boxes.values()), default=None),
                                      max((v[3] for v in boxes.values()), default=None)),
            "osm_data_date": (manifest.get("source") or {}).get("osm_data_date"),
            "built_at": manifest.get("built_at"),
            "layers": manifest.get("layers") or {},
            "simplifications": list(manifest.get("simplifications") or []),
            "note": ("Tiles inside the recorded boxes are rendered from OpenStreetMap data. "
                     "Anywhere else the surface is the procedural reference."),
        }

    def style(self) -> dict[str, Any]:
        provenance = self.provenance()
        pack = provenance["kind"] == "openstreetmap-offline-pack"
        # Declaring the source max zoom is what makes MapLibre over-zoom a real OSM tile
        # instead of requesting a zoom the pack has never rendered.
        source_max = max(MIN_ZOOM, min(MAX_ZOOM, self.pack_max_zoom() or MIN_ZOOM)) if pack else MAX_ZOOM
        attribution = " · ".join(provenance["attribution"]) if pack else PROCEDURAL_ATTRIBUTION
        return {
            "version": 8,
            "name": "RescueSwarm Offline Tactical",
            "metadata": {
                "rescue-swarm:basemap": provenance["kind"],
                "rescue-swarm:note": provenance["note"],
                "rescue-swarm:osm_data_date": provenance.get("osm_data_date"),
            },
            "sources": {
                "offline-terrain": {
                    "type": "raster",
                    "tiles": ["/api/map/tiles/{z}/{x}/{y}.png?v=3"],
                    "tileSize": TILE_SIZE,
                    "minzoom": MIN_ZOOM,
                    "maxzoom": source_max,
                    "attribution": attribution,
                }
            },
            "layers": [
                {"id": "background", "type": "background", "paint": {"background-color": "#0a1018"}},
                {
                    "id": "terrain",
                    "type": "raster",
                    "source": "offline-terrain",
                    "minzoom": MIN_ZOOM,
                    "maxzoom": MAX_ZOOM + 2,
                    "paint": {"raster-opacity": 0.92, "raster-fade-duration": 0},
                },
            ],
        }

    def _cached_path(self, z: int, x: int, y: int, suffix: str) -> Path:
        return self.cache_dir / str(z) / str(x) / f"{y}{suffix}.png"

    def _overzoom_source(self, z: int, x: int, y: int, suffix: str) -> Path | None:
        """A tile above the pack's top zoom, served by up-scaling its covered parent.

        MapLibre normally over-zooms on its own once the style declares the source max
        zoom, but the tile endpoint can also be hit directly (a print, a replay export,
        a hand-typed URL). Returning the parent OSM tile keeps those views honest; the
        alternative — falling through to the procedural field — would silently replace
        real streets with invented ones at the zoom where the operator looks closest.
        """
        pack_max = self.pack_max_zoom()
        if pack_max is None or z <= pack_max:
            return None
        shift = z - pack_max
        candidate = self._cached_path(pack_max, x >> shift, y >> shift, suffix)
        return candidate if candidate.exists() else None

    def tile(self, z: int, x: int, y: int, light: bool = False) -> bytes:
        z = max(MIN_ZOOM, min(MAX_ZOOM, z))
        key = (z, x, y, light)
        with self._lock:
            if key in self._memory:
                return self._memory[key]
        suffix = "-light" if light else ""
        path = self._cached_path(z, x, y, suffix)
        if not path.exists():
            path = self._overzoom_source(z, x, y, suffix) or path
        if path.exists():
            data = path.read_bytes()
            with self._lock:
                self._memory[key] = data
            return data
        data = self._render(z, x, y, light)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        with self._lock:
            if len(self._memory) > 256:
                self._memory.clear()
            self._memory[key] = data
        return data

    def _render(self, z: int, x: int, y: int, light: bool = False) -> bytes:
        west, south, east, north = _tile_bounds(z, x, y)
        yy, xx = np.mgrid[0:TILE_SIZE, 0:TILE_SIZE]
        lon = west + (east - west) * (xx / TILE_SIZE)
        lat = north + (south - north) * (yy / TILE_SIZE)

        # Deterministic multi-scale terrain field (metres-scale variation).
        h = (
            np.sin(lon * 4100.0) * 0.55
            + np.cos(lat * 3900.0) * 0.50
            + np.sin((lon + lat) * 1800.0) * 0.35
            + np.cos((lon - lat) * 7600.0) * 0.18
        )
        h = (h - h.min()) / (h.max() - h.min() + 1e-9)
        # Agriculture-like blocks on a 90 m grid.
        block = (np.floor((lat - 12.94) * 110_000 / 90.0) * 31.0
                 + np.floor((lon - 77.59) * 108_000 / 90.0) * 17.0)
        block_tone = (np.mod(block, 7.0) / 7.0) * 0.35

        elevation = h * 0.65 + block_tone
        if light:
            red = 222 + elevation * 24
            green = 227 + elevation * 20
            blue = 231 + elevation * 16
        else:
            red = 15 + elevation * 24
            green = 22 + elevation * 32
            blue = 31 + elevation * 40
        rgb = np.stack([red, green, blue], axis=-1).astype(np.uint8)
        image = Image.fromarray(rgb, mode="RGB")
        draw = ImageDraw.Draw(image, "RGBA")

        # 100 m reference grid and 500 m major grid.
        metres_per_pixel = max(1e-6, (east - west) * 111_320.0 * math.cos(math.radians((north + south) / 2)) / TILE_SIZE)
        minor_px = 100.0 / metres_per_pixel
        major_px = 500.0 / metres_per_pixel
        step = max(4.0, minor_px)
        offset = (x * TILE_SIZE) % step if z >= 14 else 0
        pos = -offset
        grid_tone = (70, 100, 128, 34) if light else (120, 170, 190, 20)
        major_tone = (62, 92, 120, 42) if light else (120, 170, 190, 18)
        while pos < TILE_SIZE:
            draw.line([(pos, 0), (pos, TILE_SIZE)], fill=grid_tone, width=1)
            pos += step
        step = max(6.0, major_px)
        offset = (y * TILE_SIZE) % step if z >= 13 else 0
        pos = -offset
        while pos < TILE_SIZE:
            draw.line([(0, pos), (TILE_SIZE, pos)], fill=major_tone, width=1)
            pos += step

        # A faint survey contour at fixed elevation intervals.
        band = np.mod(h * 12.0, 1.0)
        contour = (band < 0.035)
        if contour.any():
            mask = Image.fromarray((contour * 60).astype(np.uint8), mode="L")
            stroke = Image.new("RGBA", (TILE_SIZE, TILE_SIZE),
                               (90, 125, 140, 0) if light else (150, 200, 170, 0))
            stroke.putalpha(mask)
            image = Image.alpha_composite(image.convert("RGBA"), stroke).convert("RGB")
            draw = ImageDraw.Draw(image, "RGBA")

        # No text is drawn into tiles: labels would scale with zoom and are not
        # survey data. Orientation is provided by the GCS graticule and scale bar.
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
