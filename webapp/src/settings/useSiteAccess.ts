import { useMe } from "../account/useAccount";
import type { Site } from "../sites/useSites";

export interface SiteAccess {
  /** True unless the signed-in user is known to own the site, so it is also true while the account loads or after it fails. */
  readOnly: boolean;
  /** Why the settings are read-only, or null while loading or for the owner. */
  note: string | null;
}

export function useSiteAccess(site: Site): SiteAccess {
  const me = useMe();
  const readOnly = me.data?.id !== site.owner_user_id;
  if (me.isError) return { readOnly, note: "Your account details could not be loaded, so settings are read-only." };
  if (me.isSuccess && readOnly) return { readOnly, note: "Only the owner can change this site's settings." };
  return { readOnly, note: null };
}
