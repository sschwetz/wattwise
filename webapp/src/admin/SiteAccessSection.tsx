import { useState } from "react";
import { ConfirmAction } from "./ConfirmAction";
import { MutationError, headingClass, mutedClass, outlineButtonClass, sectionClass, selectClass } from "./adminUi";
import {
  type GrantRole,
  useGrantSiteAccess,
  useRemoveSiteAccess,
} from "./useAdminUserMutations";
import type { AdminUser, AdminUserSite } from "./useAdminUsers";

interface SiteOption {
  site_id: number;
  site_name: string;
}

/** Every site named anywhere in the user list, once each, by name. */
function sitesFrom(users: AdminUser[]): SiteOption[] {
  const byId = new Map<number, SiteOption>();
  for (const { sites } of users) {
    for (const { site_id, site_name } of sites) byId.set(site_id, { site_id, site_name });
  }
  return [...byId.values()].sort((a, b) => a.site_name.localeCompare(b.site_name));
}

interface RoleSelectProps {
  label: string;
  value: GrantRole;
  onChange: (role: GrantRole) => void;
  disabled?: boolean;
}

function RoleSelect({ label, value, onChange, disabled = false }: RoleSelectProps) {
  return (
    <select
      aria-label={label}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value as GrantRole)}
      className={selectClass}
    >
      <option value="manager">Manager</option>
      <option value="viewer">Viewer</option>
    </select>
  );
}

function SiteRow({ userId, site }: { userId: number; site: AdminUserSite }) {
  const grant = useGrantSiteAccess(userId);
  const remove = useRemoveSiteAccess(userId);
  const isOwner = site.role === "owner";
  return (
    <li className="space-y-2 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-ink">{site.site_name}</span>
        {isOwner ? (
          <span className={mutedClass}>Owner</span>
        ) : (
          <div className="flex items-center gap-2">
            <RoleSelect
              label={`Role for ${site.site_name}`}
              value={site.role as GrantRole}
              disabled={grant.isPending}
              onChange={(role) => grant.mutate({ siteId: site.site_id, role })}
            />
            <ConfirmAction
              label="Remove"
              ariaLabel={`Remove ${site.site_name}`}
              title="Remove site access?"
              body={`This user will no longer see ${site.site_name}.`}
              confirmLabel="Remove access"
              disabled={remove.isPending}
              onConfirm={() => remove.mutate(site.site_id)}
            />
          </div>
        )}
      </div>
      <MutationError error={grant.error ?? remove.error} fallback="Could not change site access. Please try again." />
    </li>
  );
}

function AddToSite({ userId, options }: { userId: number; options: SiteOption[] }) {
  const [siteId, setSiteId] = useState("");
  const [role, setRole] = useState<GrantRole>("viewer");
  const grant = useGrantSiteAccess(userId);

  function handleAdd() {
    grant.mutate({ siteId: Number(siteId), role }, { onSuccess: () => setSiteId("") });
  }

  return (
    <div className="space-y-2 border-t border-line pt-4">
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="Add to site" value={siteId} onChange={(e) => setSiteId(e.target.value)} className={selectClass}>
          <option value="">Choose a site</option>
          {options.map((site) => (
            <option key={site.site_id} value={site.site_id}>
              {site.site_name}
            </option>
          ))}
        </select>
        <RoleSelect label="Role for new site" value={role} onChange={setRole} disabled={grant.isPending} />
        <button type="button" disabled={siteId === "" || grant.isPending} onClick={handleAdd} className={outlineButtonClass}>
          Add
        </button>
      </div>
      <MutationError error={grant.error} fallback="Could not add the site. Please try again." />
    </div>
  );
}

export function SiteAccessSection({ user, allUsers }: { user: AdminUser; allUsers: AdminUser[] }) {
  const onSites = new Set(user.sites.map((site) => site.site_id));
  const options = sitesFrom(allUsers).filter((site) => !onSites.has(site.site_id));
  return (
    <section className={sectionClass}>
      <h2 className={headingClass}>Site access</h2>
      {user.sites.length === 0 ? (
        <p className={mutedClass}>This user has no site access.</p>
      ) : (
        <ul className="divide-y divide-line">
          {user.sites.map((site) => (
            <SiteRow key={site.site_id} userId={user.id} site={site} />
          ))}
        </ul>
      )}
      <AddToSite userId={user.id} options={options} />
    </section>
  );
}
