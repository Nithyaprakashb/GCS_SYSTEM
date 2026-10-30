"""Offline procedural tactical basemap tiles.

The map never contacts an online tile service. Tiles are generated locally from a
deterministic terrain field and cached on disk. They are a *reference* surface,
not satellite imagery or surveyed terrain; the mission geometry on top is exact.
"""

from __future__ import annotations

import io
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

    def style(self) -> dict[str, Any]:
        return {
            "version": 8,
            "name": "RescueSwarm Offline Tactical",
            "metadata": {
                "rescue-swarm:basemap": "procedural-offline",
                "rescue-swarm:note": "Local reference surface, not satellite imagery.",
            },
            "sources": {
                "offline-terrain": {
                    "type": "raster",
                    "tiles": ["/api/map/tiles/{z}/{x}/{y}.png?v=2"],
                    "tileSize": TILE_SIZE,
                    "minzoom": MIN_ZOOM,
                    "maxzoom": MAX_ZOOM,
                    "attribution": "Procedural offline reference · not satellite imagery",
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

    def tile(self, z: int, x: int, y: int, light: bool = False) -> bytes:
        z = max(MIN_ZOOM, min(MAX_ZOOM, z))
        key = (z, x, y, light)
        with self._lock:
            if key in self._memory:
                return self._memory[key]
        suffix = "-light" if light else ""
        path = self.cache_dir / str(z) / str(x) / f"{y}{suffix}.png"
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
