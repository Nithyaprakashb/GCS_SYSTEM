"""KML parsing and deterministic planner behaviour."""

from __future__ import annotations

import pytest

from rescueswarm.kml import KmlError, parse_kml
from rescueswarm.planner import generate_plan


def test_sample_boundary_geometry(sample_kml: bytes) -> None:
    aoi = parse_kml(sample_kml, "sample_search_zone_10ha.kml")
    assert aoi["valid"] is True
    assert aoi["polygons_found"] == 1
    assert aoi["area_ha"] == pytest.approx(9.97, abs=0.05)
    assert aoi["perimeter_m"] > 1300
    assert len(aoi["ring"]) == 5


def test_invalid_polygon_is_rejected() -> None:
    bad = b"""<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2">
    <Document><Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>
    77.60,12.95 77.60,12.95 77.60,12.95</coordinates></LinearRing></outerBoundaryIs>
    </Polygon></Placemark></Document></kml>"""
    with pytest.raises(KmlError):
        parse_kml(bad, "degenerate.kml")


def test_plan_lanes_and_swath(sample_kml: bytes, config) -> None:
    aoi = parse_kml(sample_kml, "sample.kml")
    plan = generate_plan(aoi, config, "SIMULATION")
    search = plan["search"]
    assert search["lane_count"] == 9
    assert search["lane_spacing_m"] == pytest.approx(22.24, abs=0.1)
    assert search["lane_spacing_m"] < search["usable_swath_m"]
    assert len(search["lanes"]) == 9
    assert plan["validation"]["ok"] is True
    assert len(plan["geofences"]) == 3
    assert plan["estimate"]["total_min_high"] > 0
    assert plan["estimate"]["within_cap"] is True
    assert plan["mission_items"]


def test_plan_hash_is_deterministic(sample_kml: bytes, config) -> None:
    aoi = parse_kml(sample_kml, "sample.kml")
    first = generate_plan(aoi, config, "SIMULATION")
    second = generate_plan(aoi, config, "SIMULATION")
    assert first["plan_hash"] == second["plan_hash"]
    assert first["search"]["lanes"] == second["search"]["lanes"]
