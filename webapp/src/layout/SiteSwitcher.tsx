import type { ChangeEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useSites } from "../sites/useSites";

const ADD_NEW_SITE = "new";

/** The site-scoped nav's way to switch between sites (or add another one) --
 * replaces the old "Switch site" link, which only ever went back to the full
 * picker page. */
export function SiteSwitcher() {
  const { siteId } = useParams<{ siteId: string }>();
  const navigate = useNavigate();
  const { data: sites } = useSites();
  // Disabled sites are left out, except the one being viewed, so the select always shows it.
  const options = (sites ?? []).filter((site) => site.enabled || String(site.id) === siteId);

  function handleChange(event: ChangeEvent<HTMLSelectElement>) {
    const value = event.target.value;
    navigate(value === ADD_NEW_SITE ? "/sites/new" : `/sites/${value}`);
  }

  return (
    <select
      aria-label="Switch site"
      value={siteId ?? ""}
      onChange={handleChange}
      className="rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
    >
      {options.map((site) => (
        <option key={site.id} value={site.id}>
          {site.enabled ? site.name : `${site.name} (disabled)`}
        </option>
      ))}
      <option value={ADD_NEW_SITE}>Add new site</option>
    </select>
  );
}
