"""WGS84 geodesy and local tangent-plane projection.

PyProj is deliberately not required at runtime: mission geometry is small
(hectares, not continents), so a spherical azimuthal-equidistant projection
around the AOI centroid is accurate to centimetres over the mission area and
keeps the application dependency-light and fully offline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

EARTH_RADIUS_M = 6371008.8


def _rad(deg: float) -> float:
    return math.radians(deg)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = _rad(lat1), _rad(lat2)
    dphi = _rad(lat2 - lat1)
    dlambda = _rad(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = _rad(lat1), _rad(lat2)
    dlambda = _rad(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination_m(lat: float, lon: float, bearing: float, distance_m: float) -> tuple[float, float]:
    """Return (lat, lon) after travelling a distance on a spherical Earth."""
    delta = distance_m / EARTH_RADIUS_M
    theta = _rad(bearing)
    phi1, lambda1 = _rad(lat), _rad(lon)
    sin_phi2 = math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    phi2 = math.asin(max(-1.0, min(1.0, sin_phi2)))
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * sin_phi2,
    )
    return math.degrees(phi2), (math.degrees(lambda2) + 540.0) % 360.0 - 180.0


@dataclass(frozen=True)
class LocalFrame:
    """Azimuthal equidistant projection anchored at (lat0, lon0), metres."""

    lat0: float
    lon0: float

    @classmethod
    def from_points(cls, points: Iterable[Sequence[float]]) -> "LocalFrame":
        pts = list(points)
        if not pts:
            raise ValueError("Cannot build a local frame without points")
        lat = sum(p[1] for p in pts) / len(pts)
        lon = sum(p[0] for p in pts) / len(pts)
        return cls(lat, lon)

    def to_local(self, lat: float, lon: float) -> tuple[float, float]:
        phi, phi0 = _rad(lat), _rad(self.lat0)
        dlambda = _rad(lon - self.lon0)
        cos_c = math.sin(phi0) * math.sin(phi) + math.cos(phi0) * math.cos(phi) * math.cos(dlambda)
        cos_c = max(-1.0, min(1.0, cos_c))
        c = math.acos(cos_c)
        k = 1.0 if c < 1e-12 else c / math.sin(c)
        x = k * EARTH_RADIUS_M * math.cos(phi) * math.sin(dlambda)
        y = k * EARTH_RADIUS_M * (
            math.cos(phi0) * math.sin(phi) - math.sin(phi0) * math.cos(phi) * math.cos(dlambda)
        )
        return x, y

    def to_wgs84(self, x: float, y: float) -> tuple[float, float]:
        rho = math.hypot(x, y)
        if rho < 1e-9:
            return self.lat0, self.lon0
        c = rho / EARTH_RADIUS_M
        phi0, lambda0 = _rad(self.lat0), _rad(self.lon0)
        sin_c, cos_c = math.sin(c), math.cos(c)
        phi = math.asin(cos_c * math.sin(phi0) + (y * sin_c * math.cos(phi0)) / rho)
        lam = lambda0 + math.atan2(
            x * sin_c,
            rho * cos_c * math.cos(phi0) - y * sin_c * math.sin(phi0),
        )
        return math.degrees(phi), (math.degrees(lam) + 540.0) % 360.0 - 180.0

    def polygon_to_local(self, ring: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
        return [self.to_local(lat, lon) for lon, lat, *_ in ring]

    def ring_to_wgs84(self, ring: Sequence[Sequence[float]]) -> list[list[float]]:
        result = []
        for x, y in ring:
            lat, lon = self.to_wgs84(x, y)
            result.append([round(lon, 9), round(lat, 9)])
        return result


def polygon_area_m2(ring: Sequence[Sequence[float]]) -> float:
    """Geodesic-ish area of a [lon, lat] ring, computed in a local frame."""
    if len(ring) < 3:
        return 0.0
    frame = LocalFrame.from_points(ring)
    pts = [frame.to_local(lat, lon) for lon, lat, *_ in ring]
    area = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def polygon_centroid(ring: Sequence[Sequence[float]]) -> tuple[float, float]:
    if not ring:
        raise ValueError("Empty ring")
    frame = LocalFrame.from_points(ring)
    pts = [frame.to_local(lat, lon) for lon, lat, *_ in ring]
    area2 = 0.0
    cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        cross = x1 * y2 - x2 * y1
        area2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area2) < 1e-9:
        lat = sum(p[1] for p in ring) / len(ring)
        lon = sum(p[0] for p in ring) / len(ring)
        return lat, lon
    cx /= 3.0 * area2
    cy /= 3.0 * area2
    return frame.to_wgs84(cx, cy)


def reduce_track(points: list[dict], min_interval_m: float = 1.0) -> list[dict]:
    """Drop telemetry points closer than min_interval_m (replay payload size)."""
    result: list[dict] = []
    for point in points:
        if not result:
            result.append(point)
            continue
        prev = result[-1]
        if haversine_m(prev["lat"], prev["lon"], point["lat"], point["lon"]) >= min_interval_m:
            result.append(point)
    return result
