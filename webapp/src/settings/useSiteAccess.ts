import type { Site } from "../sites/useSites";

export interface SiteAccess {
  /** True unless the signed-in user is an owner or a manager of the site. */
  readOnly: boolean;
  /** Why the settings are read-only, or null for an owner or a manager. */
  note: string | null;
}

export function useSiteAccess(site: Site): SiteAccess {
  const readOnly = site.my_role !== "owner" && site.my_role !== "manager";
  return { readOnly, note: readOnly ? "Only the owner or a manager can change this site's settings." : null };
}
