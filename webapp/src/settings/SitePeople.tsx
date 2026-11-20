import { errorMessage } from "../api/client";
import { ConfirmAction } from "../admin/ConfirmAction";
import { type SitePerson, useRemoveSitePerson, useSitePeople } from "../sites/useSitePeople";
import type { Site } from "../sites/useSites";

const badgeClass = "shrink-0 whitespace-nowrap rounded-full bg-surface-2 px-2 py-0.5 text-xs font-medium text-ink";

const ROLE_LABEL: Record<SitePerson["role"], string> = { owner: "Owner", manager: "Manager", viewer: "Viewer" };

function RemoveControl({ siteId, person }: { siteId: number; person: SitePerson }) {
  const remove = useRemoveSitePerson(siteId);
  return (
    <div className="space-y-1">
      <ConfirmAction
        label="Remove"
        ariaLabel={`Remove ${person.email}`}
        title={`Remove ${person.email}?`}
        body={`${person.email} will lose access to this site.`}
        confirmLabel="Remove"
        disabled={remove.isPending}
        onConfirm={() => remove.mutate(person.user_id)}
      />
      {remove.isError && (
        <p role="alert" className="text-sm text-danger">
          {errorMessage(remove.error, "Could not remove access. Please try again.")}
        </p>
      )}
    </div>
  );
}

function PersonRow({ siteId, person, canRemove }: { siteId: number; person: SitePerson; canRemove: boolean }) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-2 rounded-md border border-line p-3">
      <div className="min-w-0">
        {person.display_name && <p className="text-sm text-ink">{person.display_name}</p>}
        <p className="break-all text-sm text-ink-muted">{person.email}</p>
      </div>
      <div className="flex items-start gap-2">
        <span className={badgeClass}>{ROLE_LABEL[person.role]}</span>
        {canRemove && person.role !== "owner" && <RemoveControl siteId={siteId} person={person} />}
      </div>
    </li>
  );
}

/** Who has access to the site, for the owner and managers. Only the owner may remove someone. */
export function SitePeople({ site }: { site: Site }) {
  const people = useSitePeople(site.id);
  if (people.isPending) return <p className="text-sm text-ink-muted">Loading people...</p>;
  if (people.isError) return <p role="alert" className="text-sm text-danger">{errorMessage(people.error, "Could not load people.")}</p>;
  const canRemove = site.my_role === "owner";
  return (
    <ul className="space-y-2">
      {people.data.map((person) => (
        <PersonRow key={person.user_id} siteId={site.id} person={person} canRemove={canRemove} />
      ))}
    </ul>
  );
}
