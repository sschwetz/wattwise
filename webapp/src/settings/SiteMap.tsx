import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef } from "react";
import { type Point } from "./location";

const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
// Australia, where the energy providers this app reads from operate.
const START_VIEW: [number, number] = [-27, 134];
const START_ZOOM = 3;
const POINT_ZOOM = 10;

export interface SiteMapProps {
  point: Point | null;
  onPick: (point: Point) => void;
  disabled: boolean;
}

/** An OpenStreetMap map with one pin. A click moves the pin and reports the point. */
export default function SiteMap({ point, onPick, disabled }: SiteMapProps) {
  const element = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const pin = useRef<L.CircleMarker | null>(null);
  const handlers = useRef({ onPick, disabled });
  useEffect(() => {
    handlers.current = { onPick, disabled };
  });

  useEffect(() => {
    if (!element.current) return;
    const created = L.map(element.current).setView(START_VIEW, START_ZOOM);
    L.tileLayer(TILES, { attribution: ATTRIBUTION, maxZoom: 19 }).addTo(created);
    created.on("click", (event: L.LeafletMouseEvent) => {
      if (!handlers.current.disabled) handlers.current.onPick({ latitude: event.latlng.lat, longitude: event.latlng.lng });
    });
    map.current = created;
    return () => {
      created.remove();
      map.current = pin.current = null;
    };
  }, []);

  useEffect(() => {
    if (!map.current) return;
    pin.current?.remove();
    pin.current = null;
    if (!point) return;
    const latlng: L.LatLngTuple = [point.latitude, point.longitude];
    pin.current = L.circleMarker(latlng, { radius: 8, color: "#1a1203", weight: 2, fillColor: "#f5b50a", fillOpacity: 1 }).addTo(map.current);
    map.current.setView(latlng, Math.max(map.current.getZoom(), POINT_ZOOM));
  }, [point]);

  return <div ref={element} role="application" aria-label="Map of the site location" className="h-64 w-full rounded-md border border-line" />;
}
