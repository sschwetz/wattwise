export interface LocationText {
  latitude: string;
  longitude: string;
}

export interface Point {
  latitude: number;
  longitude: number;
}

export const EMPTY_LOCATION: LocationText = { latitude: "", longitude: "" };

/** Five decimal places is about a metre; more would only be noise from a click or a GPS fix. */
const roundDegrees = (value: number) => Number(value.toFixed(5));

export function locationText(site: { latitude: number | null; longitude: number | null }): LocationText {
  return site.latitude === null || site.longitude === null
    ? EMPTY_LOCATION
    : { latitude: String(site.latitude), longitude: String(site.longitude) };
}

export function pointText(point: Point): LocationText {
  return { latitude: String(roundDegrees(point.latitude)), longitude: String(roundDegrees(point.longitude)) };
}

function parseDegrees(text: string, limit: number): number | null {
  const value = text.trim() === "" ? Number.NaN : Number(text);
  return Number.isFinite(value) && Math.abs(value) <= limit ? value : null;
}

/** The typed point, or null when either box is empty or out of range. */
export function parsePoint(text: LocationText): Point | null {
  const latitude = parseDegrees(text.latitude, 90);
  const longitude = parseDegrees(text.longitude, 180);
  return latitude === null || longitude === null ? null : { latitude, longitude };
}

/** What to send, or an error message. Both boxes empty clears the location. `undefined` means unchanged. */
export function locationPatch(text: LocationText, saved: LocationText): Partial<{ latitude: number | null; longitude: number | null }> | string | undefined {
  if (text.latitude === saved.latitude && text.longitude === saved.longitude) return undefined;
  if (text.latitude.trim() === "" && text.longitude.trim() === "") return { latitude: null, longitude: null };
  const point = parsePoint(text);
  return point ?? "Enter a latitude from -90 to 90 and a longitude from -180 to 180, or clear both.";
}
