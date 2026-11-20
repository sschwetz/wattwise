import type { Site } from "../sites/useSites";

export interface SiteAccess {
  /** True when the signed-in user is a viewer on the site. Owners and managers can edit. */
  readOnly: boolean;
  /** Why the settings are read-only, or null for an owner or a manager. */
  note: string | null;
}

export function useSiteAccess(site: Site): SiteAccess {
  const readOnly = site.my_role === "viewer";
  return { readOnly, note: readOnly ? "Only the owner or a manager can change this site's settings." : null };
}
