import { lazy, Suspense, useState } from "react";
import { inputClass } from "../components/formStyles";
import { hintClass, secondaryButtonClass } from "../actors/styles";
import { type LocationText, parsePoint, pointText } from "./location";

const SiteMap = lazy(() => import("./SiteMap"));

export interface LocationFieldsProps {
  value: LocationText;
  onChange: (value: LocationText) => void;
  disabled: boolean;
}

function geolocationMessage(error: GeolocationPositionError): string {
  if (error.code === error.PERMISSION_DENIED) return "Location access was blocked. Allow it for this site in your browser, or click the map.";
  return "Your device could not work out where it is. Click the map or type the numbers.";
}

function UseMyLocation({ onFound }: { onFound: (value: LocationText) => void }) {
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The browser only offers location on a secure page, and some have none at all.
  if (typeof navigator === "undefined" || !("geolocation" in navigator)) return null;
  function ask() {
    setBusy(true);
    setMessage(null);
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        setBusy(false);
        onFound(pointText({ latitude: coords.latitude, longitude: coords.longitude }));
      },
      (error) => {
        setBusy(false);
        setMessage(geolocationMessage(error));
      },
      { timeout: 15_000 },
    );
  }
  return (
    <div className="space-y-1">
      <button type="button" onClick={ask} disabled={busy} className={secondaryButtonClass}>
        {busy ? "Finding your location..." : "Use my location"}
      </button>
      {message && <p role="alert" className="text-sm text-danger">{message}</p>}
    </div>
  );
}

/** Latitude and longitude boxes, a button for the device's location and a map to click. */
export function LocationFields({ value, onChange, disabled }: LocationFieldsProps) {
  const point = parsePoint(value);
  return (
    <fieldset className="space-y-3">
      <legend className="text-sm text-ink">Location</legend>
      <p className={hintClass}>Used for sunrise and sunset in schedules. Without it, sun times are estimated from the time zone.</p>
      <div className="grid grid-cols-2 gap-3">
        <label className="block space-y-1 text-sm text-ink">
          Latitude
          <input inputMode="decimal" value={value.latitude} disabled={disabled} onChange={(e) => onChange({ ...value, latitude: e.target.value })} className={inputClass} />
        </label>
        <label className="block space-y-1 text-sm text-ink">
          Longitude
          <input inputMode="decimal" value={value.longitude} disabled={disabled} onChange={(e) => onChange({ ...value, longitude: e.target.value })} className={inputClass} />
        </label>
      </div>
      {!disabled && <UseMyLocation onFound={onChange} />}
      <Suspense fallback={<div className="h-64 w-full rounded-md border border-line" />}>
        <SiteMap point={point} onPick={(picked) => onChange(pointText(picked))} disabled={disabled} />
      </Suspense>
      <p className={hintClass}>Click the map to set the point. Map data from OpenStreetMap.</p>
    </fieldset>
  );
}
