import { useState } from "react";
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

/** One chip per enabled site plus a trailing Add chip. Disabled sites stay hidden
 * until the toggle is on, except the one currently selected. */
export function SiteChips({
  sites,
  selectedId,
  hrefFor,
}: {
  sites: Site[];
  selectedId: number | undefined;
  hrefFor: (siteId: number) => string;
}) {
  const [showDisabled, setShowDisabled] = useState(false);
  const hasDisabled = sites.some((site) => !site.enabled);
  const visible = sites.filter((site) => site.enabled || showDisabled || site.id === selectedId);

  return (
    <nav aria-label="Sites" className="flex gap-2 overflow-x-auto pb-1">
      {visible.map((site) => (
        <NavLink
          key={site.id}
          to={hrefFor(site.id)}
          aria-label={site.enabled ? undefined : `${site.name} (disabled)`}
          className={chipClass(site.id === selectedId, site.enabled)}
        >
          {site.name}
        </NavLink>
      ))}
      <NavLink to="/sites/new" className={chipClass(false, true)}>
        Add
      </NavLink>
      {hasDisabled && (
        <button
          type="button"
          aria-pressed={showDisabled}
          onClick={() => setShowDisabled((on) => !on)}
          className="ml-auto shrink-0 rounded-full px-3 py-1 text-sm text-ink-muted transition-colors hover:text-ink"
        >
          {showDisabled ? "Hide disabled" : "Show disabled"}
        </button>
      )}
    </nav>
  );
}
