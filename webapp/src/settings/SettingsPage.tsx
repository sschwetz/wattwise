import { Link, Navigate, NavLink, useParams } from "react-router-dom";
import { useSites } from "../sites/useSites";
import { SiteChips } from "./SiteChips";

const TABS = [
  { id: "general", label: "General" },
  { id: "collectors", label: "Collectors" },
  { id: "tariffs", label: "Tariffs" },
  { id: "billing", label: "Billing" },
  { id: "sharing", label: "Sharing" },
] as const;

const tabLinkClass = ({ isActive }: { isActive: boolean }) =>
  `shrink-0 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
    isActive ? "bg-surface-2 text-ink" : "text-ink-muted hover:bg-surface-2 hover:text-ink"
  }`;

/** Placeholder tab content. Later tasks replace this with the real panels. */
function TabContent({ tab }: { tab: string }) {
  return <div>Tab: {tab}</div>;
}

export function SettingsPage() {
  const { siteId, tab } = useParams<{ siteId: string; tab: string }>();
  const { data: sites, isLoading } = useSites();

  if (isLoading || !sites) return <p className="p-4 text-sm text-ink-muted">Loading...</p>;

  if (sites.length === 0) {
    return (
      <div className="p-4 text-sm text-ink">
        <p>You have no sites yet.</p>
        <Link to="/sites/new" className="text-solar hover:underline">
          Add a site
        </Link>
      </div>
    );
  }

  if (siteId === undefined || tab === undefined) {
    const first = sites.find((site) => site.enabled) ?? sites[0];
    return <Navigate to={`/settings/sites/${first.id}/general`} replace />;
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 p-4">
      <h1 className="sr-only">Site settings</h1>
      <SiteChips sites={sites} selectedId={Number(siteId)} tab={tab} />
      <div className="flex flex-col gap-4 sm:flex-row">
        <nav
          aria-label="Site settings"
          className="flex shrink-0 flex-row gap-1 overflow-x-auto sm:w-44 sm:flex-col sm:overflow-visible"
        >
          {TABS.map(({ id, label }) => (
            <NavLink key={id} to={`/settings/sites/${siteId}/${id}`} className={tabLinkClass}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="min-w-0 flex-1">
          <TabContent tab={tab} />
        </div>
      </div>
    </div>
  );
}
