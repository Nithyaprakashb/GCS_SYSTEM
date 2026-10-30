"""Offline KML mission-boundary ingestion.

Parses a KML file locally (no network, no external service), extracts polygon
geometry, validates it and reports area in square metres. Mission boundaries must
be polygons; points, lines and unsupported constructs are rejected explicitly.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from typing import Any

from shapely.geometry import Polygon
from shapely.validation import explain_validity

from .geo import polygon_area_m2, polygon_centroid

MAX_KML_BYTES = 5 * 1024 * 1024
NS = "{http://www.opengis.net/kml/2.2}"


class KmlError(ValueError):
    """Raised when a KML file cannot be used as a mission boundary."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_all(element: ET.Element, name: str) -> list[ET.Element]:
    return [node for node in element.iter() if _local(node.tag) == name]


def _parse_coordinates(text: str) -> list[list[float]]:
    points: list[list[float]] = []
    for token in text.replace("\n", " ").replace("\t", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon, lat = float(parts[0]), float(parts[1])
        except ValueError as exc:
            raise KmlError(f"Invalid coordinate tuple: {token!r}") from exc
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise KmlError(f"Coordinate outside WGS84 bounds: {lon}, {lat}")
        points.append([lon, lat])
    return points


def _ring_from_boundary(boundary: ET.Element) -> list[list[float]] | None:
    coords = _find_all(boundary, "coordinates")
    if not coords or not (coords[0].text or "").strip():
        return None
    return _parse_coordinates(coords[0].text or "")


def _normalise_ring(ring: list[list[float]]) -> tuple[list[list[float]], bool]:
    if not ring:
        raise KmlError("Polygon ring has no coordinates")
    closed = ring[0] == ring[-1]
    if not closed:
        ring = ring + [ring[0]]
    if len(ring) < 4:
        raise KmlError("Polygon ring needs at least three distinct vertices")
    # Drop consecutive duplicates.
    deduped: list[list[float]] = [ring[0]]
    for point in ring[1:]:
        if point != deduped[-1]:
            deduped.append(point)
    return deduped, not closed


def parse_kml(content: bytes | str, filename: str = "mission.kml") -> dict[str, Any]:
    if isinstance(content, str):
        raw = content.encode("utf-8")
    else:
        raw = content
    if not raw:
        raise KmlError("KML file is empty")
    if len(raw) > MAX_KML_BYTES:
        raise KmlError("KML file exceeds the 5 MB import limit")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise KmlError(f"Malformed KML/XML: {exc}") from exc

    polygons = _find_all(root, "Polygon")
    if not polygons:
        raise KmlError("No Polygon found. A mission boundary must be a closed polygon.")

    candidates: list[tuple[list[list[float]], list[list[list[float]]], str]] = []
    warnings: list[str] = []
    for polygon in polygons:
        outer_nodes = [n for n in polygon if _local(n.tag) == "outerBoundaryIs"]
        if not outer_nodes:
            continue
        outer = _ring_from_boundary(outer_nodes[0])
        if outer is None:
            continue
        ring, was_open = _normalise_ring(outer)
        if was_open:
            warnings.append("Polygon was not explicitly closed; the first vertex was repeated.")
        holes: list[list[list[float]]] = []
        for inner in [n for n in polygon if _local(n.tag) == "innerBoundaryIs"]:
            inner_ring = _ring_from_boundary(inner)
            if inner_ring:
                hole, _ = _normalise_ring(inner_ring)
                holes.append(hole)
                warnings.append("Interior hole(s) were detected and will be excluded from the search area.")
        name_nodes = [n for n in polygon.iter() if _local(n.tag) == "name"]
        name = (name_nodes[0].text or "").strip() if name_nodes and name_nodes[0].text else ""
        if not name:
            placemarks = [n for n in root.iter() if _local(n.tag) == "Placemark"]
            for placemark in placemarks:
                if polygon in list(placemark.iter()):
                    names = [n for n in placemark if _local(n.tag) == "name"]
                    if names and names[0].text:
                        name = names[0].text.strip()
                    break
        candidates.append((ring, holes, name))

    if not candidates:
        raise KmlError("Polygon found but no valid outer boundary ring could be read")

    def area_of(candidate: tuple[list[list[float]], list[list[list[float]]], str]) -> float:
        try:
            return polygon_area_m2(candidate[0])
        except Exception:
            return 0.0

    candidates.sort(key=area_of, reverse=True)
    ring, holes, name = candidates[0]
    if len(candidates) > 1:
        warnings.append(
            f"{len(candidates)} polygons found; the largest was selected as the mission boundary."
        )

    frame_polygon = Polygon(
        [(lon, lat) for lon, lat, *_ in ring],
        [[(lon, lat) for lon, lat, *_ in hole] for hole in holes],
    )
    if not frame_polygon.is_valid:
        raise KmlError(f"Polygon geometry is invalid: {explain_validity(frame_polygon)}")
    if frame_polygon.area == 0:
        raise KmlError("Polygon encloses zero area")

    area = polygon_area_m2(ring)
    centroid_lat, centroid_lon = polygon_centroid(ring)
    # Compute perimeter in a local metric frame, not in degrees.
    from .geo import LocalFrame

    local_frame = LocalFrame.from_points(ring)
    local_ring = [local_frame.to_local(lat, lon) for lon, lat, *_ in ring]
    perimeter = Polygon(local_ring).length

    return {
        "filename": filename,
        "name": name or "Imported mission boundary",
        "feature_count": 1,
        "polygons_found": len(polygons),
        "ring": ring,
        "holes": holes,
        "area_m2": round(area, 1),
        "area_ha": round(area / 10_000.0, 2),
        "perimeter_m": round(perimeter, 1),
        "centroid": [round(centroid_lon, 7), round(centroid_lat, 7)],
        "valid": True,
        "warnings": warnings,
        "source_hash": hashlib.sha256(raw).hexdigest(),
        "source_bytes": len(raw),
    }
