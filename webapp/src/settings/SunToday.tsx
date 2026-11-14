import { clock } from "../actors/runFormat";
import { hintClass } from "../actors/styles";
import { type Site, useSiteSun } from "../sites/useSites";

const at = (iso: string, timezone: string) => clock(new Date(iso), timezone);

/** Today's sunrise and sunset for the saved site, so the owner can check the location. */
export function SunToday({ site }: { site: Site }) {
  const sun = useSiteSun(site);
  if (!sun.data) return null;
  const { sunrise, sunset, estimated } = sun.data;
  if (sunrise === undefined || sunset === undefined) return null;
  if (sunrise === null || sunset === null) return <p className={hintClass}>The sun does not rise or set at this location today.</p>;
  return (
    <p className={hintClass}>
      Today: sunrise {at(sunrise, site.timezone)}, sunset {at(sunset, site.timezone)}
      {estimated ? " (estimated from the time zone)" : ""}.
    </p>
  );
}
