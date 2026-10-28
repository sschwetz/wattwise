import { useMemo } from "react";

/** IANA zone names from the browser's own tz database, so no network call is needed. */
function listTimezones(): string[] {
  const zones = Intl.supportedValuesOf("timeZone");
  return zones.includes("UTC") ? zones : ["UTC", ...zones];
}

/** A site's stored zone may be missing from the browser list, so it is added rather than dropped. */
export function TimezoneSelect({
  value,
  onChange,
  disabled,
  className,
}: {
  value: string;
  onChange: (zone: string) => void;
  disabled: boolean;
  className: string;
}) {
  const zones = useMemo(() => {
    const all = listTimezones();
    return all.includes(value) ? all : [value, ...all];
  }, [value]);
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} required className={className}>
      {zones.map((zone) => (
        <option key={zone} value={zone}>
          {zone}
        </option>
      ))}
    </select>
  );
}
