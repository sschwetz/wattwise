import { NavLink } from "react-router-dom";
import type { Site } from "../sites/useSites";
import { BillingTab, TariffsTab } from "../pages/BillingPage";
import { CollectorsTab } from "../pages/SourcesPage";
import { GeneralTab } from "./GeneralTab";
import { SharingTab } from "./SharingTab";
import { SiteChips } from "./SiteChips";
import { TABS } from "./tabs";

const tabLinkClass = ({ isActive }: { isActive: boolean }) =>
  `shrink-0 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
    isActive ? "bg-surface-2 text-ink" : "text-ink-muted hover:bg-surface-2 hover:text-ink"
  }`;

/** Tab content for the selected site. */
function TabContent({ tab, site }: { tab: string; site: Site | undefined }) {
  if (!site) return <div>Tab: {tab}</div>;
  switch (tab) {
    case "general":
      return <GeneralTab key={site.id} site={site} />;
    case "collectors":
      return <CollectorsTab key={site.id} siteId={site.id} />;
    case "tariffs":
      return <TariffsTab key={site.id} siteId={site.id} />;
    case "billing":
      return <BillingTab key={site.id} siteId={site.id} />;
    case "sharing":
      return <SharingTab key={site.id} site={site} />;
    default:
      return <div>Tab: {tab}</div>;
  }
}

function TabRail({ siteId }: { siteId: number }) {
  return (
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
  );
}

export function SettingsShell({ sites, siteId, tab }: { sites: Site[]; siteId: number; tab: string }) {
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 p-4">
      <h1 className="sr-only">Site settings</h1>
      <SiteChips sites={sites} selectedId={siteId} tab={tab} />
      <div className="flex flex-col gap-4 sm:flex-row">
        <TabRail siteId={siteId} />
        <div className="min-w-0 flex-1">
          <TabContent tab={tab} site={sites.find((s) => s.id === siteId)} />
        </div>
      </div>
    </div>
  );
}
