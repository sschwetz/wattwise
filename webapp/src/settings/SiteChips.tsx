import { NavLink } from "react-router-dom";
import type { Site } from "../sites/useSites";

const chipBase = "shrink-0 rounded-full border px-3 py-1 text-sm transition-colors";

function chipClass(selected: boolean, enabled: boolean): string {
  const state = selected
    ? "border-solar bg-surface-2 font-medium text-ink"
    : "border-line text-ink-muted hover:bg-surface-2 hover:text-ink";
  const disabled = enabled ? "" : ` border-dashed ${selected ? "opacity-80" : "opacity-60 hover:opacity-100"}`;
  return `${chipBase} ${state}${disabled}`;
}

/** One chip per site (disabled sites included) plus a trailing Add chip. */
export function SiteChips({
  sites,
  selectedId,
  tab,
}: {
  sites: Site[];
  selectedId: number | undefined;
  tab: string;
}) {
  return (
    <nav aria-label="Sites" className="flex gap-2 overflow-x-auto pb-1">
      {sites.map((site) => (
        <NavLink
          key={site.id}
          to={`/settings/sites/${site.id}/${tab}`}
          aria-label={site.enabled ? undefined : `${site.name} (disabled)`}
          className={chipClass(site.id === selectedId, site.enabled)}
        >
          {site.name}
        </NavLink>
      ))}
      <NavLink to="/sites/new" className={chipClass(false, true)}>
        Add
      </NavLink>
    </nav>
  );
}
