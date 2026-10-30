import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { type Map as MapLibreMap } from "maplibre-gl";
import { Crosshair, Layers, Maximize2 } from "lucide-react";
import type { Aoi, Geofence, MissionPlan, SafetySnapshot, Survivor, Vehicle } from "../lib/types";

interface MapProps {
  aoi?: Aoi | null;
  plan?: MissionPlan | null;
  vehicles?: Record<string, Vehicle> | Vehicle[];
  survivors?: Survivor[];
  safety?: SafetySnapshot | null;
  aircraftOverride?: Record<string, { lat: number; lon: number; heading_deg?: number; connected?: boolean; role?: string }>;
  trails?: Record<string, { lon: number; lat: number }[]>;
  onSelectSurvivor?: (survivor: Survivor) => void;
  onExpand?: () => void;
  className?: string;
}

function ringToCoords(ring: [number, number][] | undefined): [number, number][] {
  if (!ring || ring.length === 0) return [];
  const coords = ring.map(([lon, lat]) => [lon, lat] as [number, number]);
  const first = coords[0];
  const last = coords[coords.length - 1];
  if (first[0] !== last[0] || first[1] !== last[1]) coords.push([first[0], first[1]]);
  return coords;
}

function circlePolygon(lat: number, lon: number, radiusM: number): [number, number][] {
  const points: [number, number][] = [];
  const earth = 6371008.8;
  for (let i = 0; i <= 64; i += 1) {
    const bearing = (i / 64) * Math.PI * 2;
    const angular = radiusM / earth;
    const lat1 = (lat * Math.PI) / 180;
    const lon1 = (lon * Math.PI) / 180;
    const lat2 = Math.asin(Math.sin(lat1) * Math.cos(angular) + Math.cos(lat1) * Math.sin(angular) * Math.cos(bearing));
    const lon2 = lon1 + Math.atan2(Math.sin(bearing) * Math.sin(angular) * Math.cos(lat1), Math.cos(angular) - Math.sin(lat1) * Math.sin(lat2));
    points.push([((lon2 * 180) / Math.PI + 540) % 360 - 180, (lat2 * 180) / Math.PI]);
  }
  return points;
}

function normaliseVehicles(vehicles?: Record<string, Vehicle> | Vehicle[]): Vehicle[] {
  if (!vehicles) return [];
  return Array.isArray(vehicles) ? vehicles : Object.values(vehicles);
}

function emptyCollection(): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

export function TacticalMap({
  aoi,
  plan,
  vehicles,
  survivors = [],
  safety,
  aircraftOverride,
  trails,
  onSelectSurvivor,
  onExpand,
  className,
}: MapProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markerRefs = useRef<Record<string, maplibregl.Marker>>({});
  const survivorMarkers = useRef<Record<string, maplibregl.Marker>>({});
  const fitted = useRef(false);
  const [ready, setReady] = useState(false);
  const [styleError, setStyleError] = useState<string | null>(null);

  const aircraft = useMemo(() => {
    const base = normaliseVehicles(vehicles);
    return base.map((vehicle) => {
      const override = aircraftOverride?.[vehicle.callsign];
      return {
        ...vehicle,
        lat: override?.lat ?? vehicle.lat,
        lon: override?.lon ?? vehicle.lon,
        heading_deg: override?.heading_deg ?? vehicle.heading_deg,
        connected: override?.connected ?? vehicle.connected,
        role: (override?.role as "SEARCH" | "DELIVERY" | undefined) ?? vehicle.role,
      };
    });
  }, [vehicles, aircraftOverride]);

  // Initialise once.
  useEffect(() => {
    if (!container.current || mapRef.current) return;
    let cancelled = false;

    const fallback = {
      version: 8 as const,
      sources: {},
      layers: [{ id: "bg", type: "background" as const, paint: { "background-color": "#08111a" } }],
    };

    const boot = async () => {
      let style: maplibregl.StyleSpecification = fallback;
      try {
        const response = await fetch("/api/map/style");
        if (response.ok) {
          const fetched = (await response.json()) as maplibregl.StyleSpecification;
          if (!fetched.glyphs) delete (fetched as { glyphs?: unknown }).glyphs;
          style = fetched;
        } else setStyleError("Offline basemap unavailable");
      } catch {
        setStyleError("Offline basemap unavailable");
      }
      if (cancelled || !container.current) return;
      const map = new maplibregl.Map({
        container: container.current,
        style,
        center: [77.5991, 12.9487],
        zoom: 15.2,
        pitch: 0,
        bearing: 0,
        attributionControl: false,
        maxZoom: 18,
        minZoom: 10,
      });
      mapRef.current = map;
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
      map.addControl(new maplibregl.ScaleControl({ maxWidth: 90, unit: "metric" }), "bottom-left");
      map.on("load", () => {
        if (cancelled) return;
        map.addSource("aoi", { type: "geojson", data: emptyCollection() });
        map.addLayer({
          id: "aoi-fill",
          type: "fill",
          source: "aoi",
          paint: { "fill-color": "#5ad1c8", "fill-opacity": 0.055 },
        });
        map.addLayer({
          id: "aoi-line",
          type: "line",
          source: "aoi",
          paint: { "line-color": "#5ad1c8", "line-width": 1.4, "line-opacity": 0.8, "line-dasharray": [4, 3] },
        });
        map.addSource("lanes", { type: "geojson", data: emptyCollection() });
        map.addLayer({
          id: "lanes-line",
          type: "line",
          source: "lanes",
          paint: { "line-color": "#f0a03c", "line-width": 1.15, "line-opacity": 0.72 },
        });
        map.addSource("fences", { type: "geojson", data: emptyCollection() });
        map.addLayer({
          id: "fences-line",
          type: "line",
          source: "fences",
          paint: { "line-color": ["get", "colour"], "line-width": 1, "line-opacity": 0.42, "line-dasharray": [2, 4] },
        });
        map.addSource("pad", { type: "geojson", data: emptyCollection() });
        map.addLayer({
          id: "pad-fill",
          type: "fill",
          source: "pad",
          paint: { "fill-color": "#f0a03c", "fill-opacity": 0.1 },
        });
        map.addLayer({
          id: "pad-line",
          type: "line",
          source: "pad",
          paint: { "line-color": "#f0a03c", "line-width": 1.2, "line-dasharray": [2, 2] },
        });
        map.addSource("links", { type: "geojson", data: emptyCollection() });
        map.addLayer({
          id: "links-line",
          type: "line",
          source: "links",
          paint: { "line-color": "#65a8f5", "line-width": 1, "line-opacity": 0.35, "line-dasharray": [1, 3] },
        });
        map.addSource("trails", { type: "geojson", data: emptyCollection() });
        map.addLayer({
          id: "trails-line",
          type: "line",
          source: "trails",
          paint: {
            "line-color": ["match", ["get", "callsign"], "INS-01", "#5ad1c8", "DEL-01", "#f0a03c", "DEL-02", "#9b8cf0", "#8fa0b5"],
            "line-width": 1.3,
            "line-opacity": 0.55,
          },
        });
        setReady(true);
      });
    };
    void boot();
    return () => {
      cancelled = true;
      Object.values(markerRefs.current).forEach((marker) => marker.remove());
      Object.values(survivorMarkers.current).forEach((marker) => marker.remove());
      markerRefs.current = {};
      survivorMarkers.current = {};
      mapRef.current?.remove();
      mapRef.current = null;
      setReady(false);
    };
  }, []);

  // Basemap palette follows the station theme; geometry colours stay constant.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const apply = () => {
      const source = map.getSource("offline-terrain") as maplibregl.RasterTileSource | undefined;
      const light = document.documentElement.dataset.theme === "light";
      source?.setTiles([`/api/map/tiles/{z}/{x}/{y}.png?v=2${light ? "&theme=light" : ""}`]);
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, [ready]);

  // AOI geometry.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !map.getSource("aoi")) return;
    const ring = ringToCoords(aoi?.ring);
    const data: GeoJSON.FeatureCollection = ring.length
      ? {
          type: "FeatureCollection",
          features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } }],
        }
      : emptyCollection();
    (map.getSource("aoi") as maplibregl.GeoJSONSource).setData(data);
    if (ring.length && !fitted.current) {
      const bounds = ring.reduce(
        (acc, [lon, lat]) => acc.extend([lon, lat]),
        new maplibregl.LngLatBounds([ring[0][0], ring[0][1]], [ring[0][0], ring[0][1]]),
      );
      map.fitBounds(bounds, { padding: { top: 60, bottom: 45, left: 55, right: 55 }, duration: 700 });
      fitted.current = true;
    }
  }, [aoi, ready]);

  // Plan lanes, geofences and pad.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const laneSource = map.getSource("lanes") as maplibregl.GeoJSONSource | undefined;
    if (laneSource) {
      const features: GeoJSON.Feature[] = (plan?.search?.lanes ?? []).map((lane) => ({
        type: "Feature",
        properties: {},
        geometry: { type: "LineString", coordinates: lane.map((point) => [point.lon, point.lat]) },
      }));
      laneSource.setData({ type: "FeatureCollection", features });
    }
    const fenceSource = map.getSource("fences") as maplibregl.GeoJSONSource | undefined;
    if (fenceSource) {
      const colours = ["#5ad1c8", "#f0a03c", "#9b8cf0"];
      const features: GeoJSON.Feature[] = Object.values(plan?.geofences ?? {}).flatMap((fence: Geofence, index) => {
        const raw = (fence.ring ?? fence.polygon ?? []) as [number, number][];
        const coordinates = ringToCoords(raw);
        if (!coordinates.length) return [];
        return [
          {
            type: "Feature" as const,
            properties: { colour: colours[index % colours.length], callsign: fence.callsign },
            geometry: { type: "Polygon" as const, coordinates: [coordinates] },
          },
        ];
      });
      fenceSource.setData({ type: "FeatureCollection", features });
    }
    const padSource = map.getSource("pad") as maplibregl.GeoJSONSource | undefined;
    if (padSource) {
      const pad = plan?.pad;
      padSource.setData(
        pad
          ? {
              type: "FeatureCollection",
              features: [
                {
                  type: "Feature",
                  properties: {},
                  geometry: { type: "Polygon", coordinates: [circlePolygon(pad.lat, pad.lon, pad.radius_m || 1.83)] },
                },
              ],
            }
          : emptyCollection(),
      );
    }
  }, [plan, ready]);

  // Aircraft DOM markers.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const seen = new Set<string>();
    for (const vehicle of aircraft) {
      if (vehicle.lat === null || vehicle.lon === null) continue;
      seen.add(vehicle.callsign);
      let marker = markerRefs.current[vehicle.callsign];
      if (!marker) {
        const element = document.createElement("div");
        element.className = `map-marker ${vehicle.role === "DELIVERY" ? "del" : "ins"}`;
        element.innerHTML = `<div class="craft"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M2 12h20"/><circle cx="12" cy="12" r="3"/></svg><span class="heading"></span></div><span class="tag"></span>`;
        marker = new maplibregl.Marker({ element, anchor: "center" }).setLngLat([vehicle.lon, vehicle.lat]).addTo(map);
        markerRefs.current[vehicle.callsign] = marker;
      }
      marker.setLngLat([vehicle.lon, vehicle.lat]);
      const element = marker.getElement();
      element.className = `map-marker ${vehicle.role === "DELIVERY" ? "del" : "ins"} ${vehicle.connected ? "" : "offline"}`;
      const tag = element.querySelector(".tag");
      if (tag) tag.textContent = vehicle.callsign;
      const heading = element.querySelector<HTMLElement>(".heading");
      if (heading) heading.style.transform = `rotate(${vehicle.heading_deg ?? 0}deg)`;
    }
    for (const [callsign, marker] of Object.entries(markerRefs.current)) {
      if (!seen.has(callsign)) {
        marker.remove();
        delete markerRefs.current[callsign];
      }
    }
  }, [aircraft, ready]);

  // Survivor DOM markers.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const seen = new Set<string>();
    for (const survivor of survivors) {
      seen.add(survivor.survivor_id);
      const tone =
        survivor.status === "DELIVERED"
          ? "delivered"
          : survivor.status === "UNKNOWN"
            ? "unknown"
            : survivor.status === "UNSERVED" || survivor.status === "REJECTED"
              ? "unserved"
              : survivor.status === "ASSIGNED" || survivor.status === "IN_DELIVERY"
                ? "assigned"
                : "";
      let marker = survivorMarkers.current[survivor.survivor_id];
      if (!marker) {
        const element = document.createElement("div");
        element.className = `marker-survivor ${tone}`;
        element.textContent = survivor.survivor_id.replace("S-", "");
        element.title = `${survivor.survivor_id} · ${survivor.status}`;
        element.style.cursor = onSelectSurvivor ? "pointer" : "default";
        if (onSelectSurvivor) element.addEventListener("click", () => onSelectSurvivor(survivor));
        marker = new maplibregl.Marker({ element, anchor: "center" })
          .setLngLat([survivor.lon, survivor.lat])
          .addTo(map);
        survivorMarkers.current[survivor.survivor_id] = marker;
      }
      marker.setLngLat([survivor.lon, survivor.lat]);
      const element = marker.getElement();
      element.className = `marker-survivor ${tone}`;
      element.title = `${survivor.survivor_id} · ${survivor.status}`;
    }
    for (const [id, marker] of Object.entries(survivorMarkers.current)) {
      if (!seen.has(id)) {
        marker.remove();
        delete survivorMarkers.current[id];
      }
    }
  }, [survivors, ready, onSelectSurvivor]);

  // Assignment drop lines.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource("links") as maplibregl.GeoJSONSource | undefined;
    if (!source) return;
    const features: GeoJSON.Feature[] = survivors
      .filter((s) => s.drop_lat && s.drop_lon && s.status !== "DUPLICATE")
      .map((s) => ({
        type: "Feature",
        properties: {},
        geometry: {
          type: "LineString",
          coordinates: [
            [s.lon, s.lat],
            [s.drop_lon, s.drop_lat],
          ],
        },
      }));
    source.setData({ type: "FeatureCollection", features });
  }, [survivors, ready]);

  // Recorded trails (replay).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource("trails") as maplibregl.GeoJSONSource | undefined;
    if (!source) return;
    const features: GeoJSON.Feature[] = Object.entries(trails ?? {})
      .filter(([, points]) => points.length > 1)
      .map(([callsign, points]) => ({
        type: "Feature",
        properties: { callsign },
        geometry: { type: "LineString", coordinates: points.map((point) => [point.lon, point.lat]) },
      }));
    source.setData({ type: "FeatureCollection", features });
  }, [trails, ready]);

  const recenter = () => {
    const map = mapRef.current;
    if (!map) return;
    if (aoi?.ring?.length) {
      const bounds = aoi.ring.reduce(
        (acc, [lon, lat]) => acc.extend([lon, lat]),
        new maplibregl.LngLatBounds([aoi.ring[0][0], aoi.ring[0][1]], [aoi.ring[0][0], aoi.ring[0][1]]),
      );
      map.fitBounds(bounds, { padding: 60, duration: 600 });
    } else if (plan?.pad) {
      map.flyTo({ center: [plan.pad.lon, plan.pad.lat], zoom: 16, duration: 600 });
    }
  };

  const area = aoi?.area_ha;
  const padClear = safety?.pad?.clear;

  return (
    <div className={`map-panel ${className ?? ""}`}>
      <div ref={container} style={{ position: "absolute", inset: 0 }} />
      <div className="map-overlay-top">
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <span className="map-chip">
            <Layers size={12} /> Local tiles · no internet
          </span>
          {area && <span className="map-chip mono">{area.toFixed(3)} ha</span>}
          {padClear !== undefined && (
            <span className="map-chip" style={{ color: padClear ? "var(--ok)" : "var(--danger)" }}>
              PAD {padClear ? "CLEAR" : "OCCUPIED"}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button className="map-chip" onClick={recenter} title="Fit mission area">
            <Crosshair size={12} /> Fit
          </button>
          {onExpand && (
            <button className="map-chip" onClick={onExpand} title="Expand map">
              <Maximize2 size={12} />
            </button>
          )}
        </div>
      </div>
      <div className="map-overlay-bottom">
        <div className="map-legend">
          <span className="lg"><i className="swatch" style={{ background: "var(--signal)" }} /> Search</span>
          <span className="lg"><i className="swatch" style={{ background: "var(--accent)" }} /> Delivery</span>
          <span className="lg"><i className="swatch" style={{ background: "var(--ok)" }} /> Delivered</span>
          <span className="lg"><i className="swatch" style={{ background: "var(--warn)" }} /> Unknown</span>
          <span className="lg"><i className="swatch" style={{ background: "var(--info)" }} /> Assigned</span>
        </div>
        <div className="map-attrib">
          Procedural offline reference
          <br />
          not satellite imagery
        </div>
      </div>
      {styleError && (
        <div style={{ position: "absolute", left: "50%", top: "50%", transform: "translate(-50%,-50%)", color: "var(--muted)", fontSize: 12 }}>
          {styleError}
        </div>
      )}
    </div>
  );
}
