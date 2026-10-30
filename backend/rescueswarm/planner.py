"""Automatic search-mission and geofence planning from a validated KML polygon.

The planner is deterministic: identical KML, configuration and plan version
produce an identical plan hash. It generates the lawnmower search path along the
polygon's long axis, insets lane ends, derives per-aircraft fences, and exposes
every assumption used in time and energy estimates.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import nearest_points

from .config import Config
from .geo import LocalFrame, haversine_m
from .util import sha256_json

PLANNER_VERSION = "1.2.0"


class PlanningError(ValueError):
    pass


def _rotate(x: float, y: float, angle: float) -> tuple[float, float]:
    c, s = math.cos(angle), math.sin(angle)
    return x * c - y * s, x * s + y * c


def _unrotate(x: float, y: float, angle: float) -> tuple[float, float]:
    c, s = math.cos(angle), math.sin(angle)
    return x * c + y * s, -x * s + y * c


def _iter_segments(geometry: Any) -> list[list[tuple[float, float]]]:
    segments: list[list[tuple[float, float]]] = []
    if geometry.is_empty:
        return segments
    if isinstance(geometry, LineString):
        segments.append(list(geometry.coords))
    elif isinstance(geometry, (MultiLineString, GeometryCollection)):
        for part in geometry.geoms:
            segments.extend(_iter_segments(part))
    return segments


def _trim_segment(
    coords: list[tuple[float, float]], inset: float
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    if len(coords) < 2:
        return None
    start, end = coords[0], coords[-1]
    length = math.dist(start, end)
    if length <= inset * 2.0 + 1.0:
        return None
    dx, dy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
    return (
        (start[0] + dx * inset, start[1] + dy * inset),
        (end[0] - dx * inset, end[1] - dy * inset),
    )


def _long_axis_angle(polygon: Polygon) -> float:
    rect = polygon.minimum_rotated_rectangle
    coords = list(rect.exterior.coords)[:4]
    if len(coords) < 4:
        return 0.0
    edges = []
    for index in range(4):
        x1, y1 = coords[index]
        x2, y2 = coords[(index + 1) % 4]
        edges.append((math.hypot(x2 - x1, y2 - y1), math.atan2(y2 - y1, x2 - x1)))
    longest = max(edges, key=lambda edge: edge[0])
    angle = longest[1]
    # Normalise to [-45°, 45°] so lane headings stay stable regardless of which
    # edge of the rotated rectangle was selected.
    while angle > math.pi / 4:
        angle -= math.pi / 2
    while angle < -math.pi / 4:
        angle += math.pi / 2
    return angle


def _pad_validation(config: Config, ring: list[list[float]], frame: LocalFrame, polygon: Polygon) -> dict:
    mission_cfg = config.get("mission", default={})
    pad_lat = float(mission_cfg["pad_lat"])
    pad_lon = float(mission_cfg["pad_lon"])
    px, py = frame.to_local(pad_lat, pad_lon)
    pad_point = Point(px, py)
    inside = polygon.contains(pad_point)
    distance = pad_point.distance(polygon.boundary)
    warnings: list[str] = []
    if inside:
        warnings.append("Launch pad lies inside the search polygon; confirm the pad is clear of the search lanes.")
    if distance > 300:
        warnings.append("Launch pad is more than 300 m from the boundary; transit time may be underestimated.")
    return {
        "name": mission_cfg.get("pad_name", "PAD-A"),
        "lat": pad_lat,
        "lon": pad_lon,
        "inside_aoi": bool(inside),
        "distance_to_boundary_m": round(distance, 1),
        "radius_m": mission_cfg.get("pad_radius_m", 1.83),
        "warnings": warnings,
        "evidence": "CONFIGURED",
    }


def generate_plan(aoi: dict[str, Any], config: Config, mode: str = "SIMULATION") -> dict[str, Any]:
    ring = aoi["ring"]
    if len(ring) < 4:
        raise PlanningError("Mission polygon is incomplete")
    mission_cfg = config.get("mission", default={})
    aircraft = config.search_vehicle()
    alt = float(aircraft["search_alt_m"])
    speed = float(aircraft["search_speed_ms"])
    hfov = float(aircraft["camera_hfov_deg"])
    usable_fraction = float(mission_cfg.get("usable_swath_fraction", 0.8))
    inset = float(mission_cfg.get("lane_inset_m", 3.0))

    frame = LocalFrame.from_points(ring)
    local_ring = [frame.to_local(lat, lon) for lon, lat, *_ in ring]
    polygon = Polygon(local_ring)
    if not polygon.is_valid or polygon.area < float(mission_cfg.get("min_area_m2", 1000)):
        raise PlanningError("Mission polygon is invalid or too small for lane generation")

    angle = _long_axis_angle(polygon)
    rotated_pts = [_rotate(x, y, -angle) for x, y in local_ring]
    rotated_poly = Polygon(rotated_pts)
    minx, miny, maxx, maxy = rotated_poly.bounds
    short_span = maxy - miny
    long_span = maxx - minx
    full_swath = 2.0 * alt * math.tan(math.radians(hfov / 2.0))
    usable_swath = full_swath * usable_fraction
    lane_count = max(1, math.ceil(short_span / usable_swath))
    if lane_count > 80:
        raise PlanningError(f"Planned lane count ({lane_count}) exceeds the 80-lane sanity limit")
    spacing = short_span / lane_count
    offsets = [miny + spacing * (index + 0.5) for index in range(lane_count)]

    lanes: list[list[dict[str, float]]] = []
    path_length = 0.0
    total_run_length = 0.0
    transitions = 0
    previous_end: tuple[float, float] | None = None

    for lane_index, y in enumerate(offsets):
        scan = LineString([(minx - spacing, y), (maxx + spacing, y)])
        clipped = rotated_poly.intersection(scan)
        segments = []
        for coords in _iter_segments(clipped):
            trimmed = _trim_segment(coords, inset)
            if trimmed:
                segments.append(trimmed)
        if not segments:
            continue
        segments.sort(key=lambda seg: min(seg[0][0], seg[1][0]))
        if lane_index % 2 == 1:
            segments.reverse()
        lane_points: list[dict[str, float]] = []
        for seg_index, (start, end) in enumerate(segments):
            if lane_index % 2 == 1:
                start, end = end, start
            # Connect disjoint pieces and lanes; these are real flight legs.
            if previous_end is not None:
                path_length += math.dist(previous_end, start)
                transitions += 1
            path_length += math.dist(start, end)
            total_run_length += math.dist(start, end)
            lane_points.append(_local_to_payload(start, angle, frame))
            lane_points.append(_local_to_payload(end, angle, frame))
            previous_end = end
            if seg_index < len(segments) - 1:
                transitions += 1
        lanes.append(lane_points)

    if not lanes:
        raise PlanningError("No search lanes could be generated inside the polygon")

    waypoints = [point for lane in lanes for point in lane]
    turn_time = float(mission_cfg.get("turn_time_s", 3.0))
    search_time_s = path_length / speed + transitions * turn_time
    search_time_min = search_time_s / 60.0

    takeoff_s = float(mission_cfg.get("takeoff_time_s", 30.0))
    recovery_s = float(mission_cfg.get("recovery_time_s", 105.0))
    delivery_tail = mission_cfg.get("delivery_tail_min", [1.0, 2.5])
    total_min_low = (takeoff_s + search_time_s + delivery_tail[0] * 60 + recovery_s) / 60.0
    total_min_high = (takeoff_s + search_time_s + delivery_tail[1] * 60 + recovery_s) / 60.0
    time_cap = float(mission_cfg.get("time_cap_min", 30.0))
    target = float(mission_cfg.get("target_time_min", 15.0))

    pad = _pad_validation(config, ring, frame, polygon)
    boundary_distance = pad["distance_to_boundary_m"]

    max_alt = max(
        float(config.vehicle(c)["rtl_alt_m"]) for c in config.callsigns
    ) + 5.0
    geofences = {}
    for callsign in config.callsigns:
        profile = config.vehicle(callsign)
        geofences[callsign] = {
            "callsign": callsign,
            "sysid": profile["sysid"],
            "type": "INCLUSION_POLYGON",
            "polygon": ring,
            "max_alt_m": round(max(float(profile["rtl_alt_m"]), float(profile.get("transit_alt_m", 0))) + 3.0, 1),
            "action": "RTL",
            "fence_margin_m": 0.0,
            "gcs_warning_margin_m": float(mission_cfg.get("boundary_warn_m", 2.0)),
        }

    search_profile = {
        "callsign": aircraft["callsign"],
        "sysid": aircraft["sysid"],
        "altitude_m": alt,
        "speed_ms": speed,
        "camera_hfov_deg": hfov,
        "full_swath_m": round(full_swath, 2),
        "usable_swath_m": round(usable_swath, 2),
        "lane_spacing_m": round(spacing, 2),
        "lane_count": len(lanes),
        "lane_inset_m": inset,
        "lanes": lanes,
        "waypoint_count": len(waypoints),
        "path_length_m": round(path_length, 1),
        "total_run_length_m": round(total_run_length, 1),
        "transitions": transitions,
        "search_time_s": round(search_time_s, 1),
        "long_axis_deg": round(math.degrees(angle) % 180.0, 2),
        "evidence": "CALCULATED",
    }

    # MAVLink-shaped mission items (relative altitude, WGS84). These are a plan
    # artifact; real upload and read-back are handled by the mission adapter.
    mission_items = [
        {
            "seq": 0,
            "command": 22,  # NAV_TAKEOFF
            "frame": 6,  # MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
            "lat": round(pad["lat"], 7),
            "lon": round(pad["lon"], 7),
            "alt_m": alt,
            "label": "Takeoff at launch pad",
        }
    ]
    for index, point in enumerate(waypoints, start=1):
        mission_items.append(
            {
                "seq": index,
                "command": 16,  # NAV_WAYPOINT
                "frame": 6,
                "lat": point["lat"],
                "lon": point["lon"],
                "alt_m": alt,
                "label": f"Search lane {((index - 1) // 2) + 1}",
            }
        )
    mission_items.append(
        {
            "seq": len(mission_items),
            "command": 20,  # NAV_RETURN_TO_LAUNCH
            "frame": 6,
            "lat": round(pad["lat"], 7),
            "lon": round(pad["lon"], 7),
            "alt_m": 0.0,
            "label": "Return to launch",
        }
    )

    battery_search = search_time_min * float(mission_cfg.get("battery_search_pct_per_min", 3.2))
    kits_per_delivery_drone = max((int(a.get("kits", 0)) for a in config.delivery_vehicles()), default=5)
    delivery_min = kits_per_delivery_drone * (35.0 / 60.0)  # 15 s leg + 20 s service planning allowance
    battery_delivery = delivery_min * float(mission_cfg.get("battery_delivery_pct_per_min", 2.6))
    reserve = float(mission_cfg.get("battery_reserve_pct", 22.0))

    warnings: list[str] = list(aoi.get("warnings", []))
    warnings.extend(pad["warnings"])
    if total_min_high > target:
        warnings.append(
            f"Upper estimate {total_min_high:.1f} min exceeds the {target:.0f} min target (still inside the {time_cap:.0f} min cap)."
        )
    if total_min_high > time_cap:
        warnings.append(f"Upper estimate exceeds the {time_cap:.0f} min mission cap.")
    area_ha = aoi.get("area_ha", 0)
    if abs(area_ha - 10.0) > 2.0:
        warnings.append(f"Imported area {area_ha:.2f} ha differs from the 10 ha reference by more than 20%.")

    validation_errors: list[str] = []
    if len(waypoints) < 2:
        validation_errors.append("Fewer than two lane-end waypoints were generated.")
    for point in waypoints:
        lx, ly = frame.to_local(point["lat"], point["lon"])
        if not polygon.buffer(0.5).contains(Point(lx, ly)):
            validation_errors.append("A generated waypoint lies outside the mission polygon.")
            break
    if total_min_high > time_cap:
        validation_errors.append("Planned mission exceeds the mission time cap.")

    plan_payload = {
        "planner_version": PLANNER_VERSION,
        "mode": mode,
        "config_version": config.get("profile_version"),
        "kml_hash": aoi.get("source_hash"),
        "search": search_profile,
        "mission_items": mission_items,
        "geofences": geofences,
        "pad": pad,
        "max_fence_alt_m": max_alt,
    }
    plan_hash = sha256_json(plan_payload)

    return {
        "plan_id": "PLAN-" + plan_hash[:12].upper(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "planner_version": PLANNER_VERSION,
        "plan_hash": plan_hash,
        "mode": mode,
        "aoi": aoi,
        "frame": {"lat0": frame.lat0, "lon0": frame.lon0},
        "search": search_profile,
        "mission_items": mission_items,
        "geofences": geofences,
        "pad": pad,
        "estimate": {
            "takeoff_s": takeoff_s,
            "search_s": round(search_time_s, 1),
            "delivery_tail_min": delivery_tail,
            "recovery_s": recovery_s,
            "total_min_low": round(total_min_low, 2),
            "total_min_high": round(total_min_high, 2),
            "total_min_display": f"{total_min_low:.1f}–{total_min_high:.1f} min",
            "time_cap_min": time_cap,
            "target_time_min": target,
            "within_cap": total_min_high <= time_cap,
            "within_target": total_min_high <= target,
            "inputs": {
                "search_altitude_m": alt,
                "search_speed_ms": speed,
                "turn_time_s": turn_time,
                "lane_inset_m": inset,
                "delivery_speed_ms": float(config.delivery_vehicles()[0]["delivery_speed_ms"]),
                "service_time_s": float(mission_cfg.get("service_time_s", 25.0)),
            },
            "evidence": "CALCULATED / ASSUMED — not flight-validated",
        },
        "energy": {
            "ins_search_min": round(search_time_min, 2),
            "ins_search_pct": round(battery_search, 1),
            "delivery_estimate_pct": round(battery_delivery, 1),
            "reserve_pct": reserve,
            "evidence": "CALCULATED on an unverified power model",
        },
        "delivery": [
            {
                "callsign": profile["callsign"],
                "sysid": profile["sysid"],
                "transit_alt_m": profile["transit_alt_m"],
                "drop_height_m": profile["drop_height_m"],
                "kits": profile["kits"],
                "staging": {"lat": pad["lat"], "lon": pad["lon"]},
                "mode": "GUIDED targets allocated at runtime",
            }
            for profile in config.delivery_vehicles()
        ],
        "validation": {
            "ok": not validation_errors,
            "errors": validation_errors,
            "warnings": sorted(set(warnings)),
        },
        "plan_hash": plan_hash,
        "evidence": "CALCULATED — planning artifact, not measured flight performance",
    }


def _local_to_payload(point: tuple[float, float], angle: float, frame: LocalFrame) -> dict[str, float]:
    x, y = _unrotate(point[0], point[1], -angle)
    lat, lon = frame.to_wgs84(x, y)
    return {"lat": round(lat, 7), "lon": round(lon, 7)}


def distance_to_boundary_m(aoi: dict[str, Any], lat: float, lon: float) -> float:
    ring = aoi["ring"]
    frame = LocalFrame.from_points(ring)
    poly = Polygon([frame.to_local(la, lo) for lo, la, *_ in ring])
    point = Point(*frame.to_local(lat, lon))
    return point.distance(poly.boundary)


def move_inside_boundary(aoi: dict[str, Any], lat: float, lon: float, inset_m: float) -> tuple[float, float]:
    """Move a point inward when it sits within inset_m of the fence boundary."""
    ring = aoi["ring"]
    frame = LocalFrame.from_points(ring)
    poly = Polygon([frame.to_local(la, lo) for lo, la, *_ in ring])
    point = Point(*frame.to_local(lat, lon))
    if poly.contains(point) and point.distance(poly.boundary) >= inset_m:
        return lat, lon
    nearest = nearest_points(poly.boundary, point)[0]
    direction = (point.x - nearest.x, point.y - nearest.y)
    norm = math.hypot(*direction)
    if norm < 1e-9:
        direction, norm = (1.0, 0.0), 1.0
    target = Point(
        nearest.x + direction[0] / norm * inset_m,
        nearest.y + direction[1] / norm * inset_m,
    )
    if not poly.contains(target):
        target = poly.representative_point()
    new_lat, new_lon = frame.to_wgs84(target.x, target.y)
    return round(new_lat, 7), round(new_lon, 7)


def point_inside_aoi(aoi: dict[str, Any], lat: float, lon: float) -> bool:
    ring = aoi["ring"]
    frame = LocalFrame.from_points(ring)
    poly = Polygon([frame.to_local(la, lo) for lo, la, *_ in ring])
    return poly.contains(Point(*frame.to_local(lat, lon)))


def aoi_distance_m(aoi: dict[str, Any], lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return haversine_m(lat1, lon1, lat2, lon2)
