import { BackLink } from "../layout/BackLink";
import { SideRail } from "../layout/SideRail";
import type { Site } from "../sites/useSites";
import { BillingTab, TariffsTab } from "../pages/BillingPage";
import { CollectorsTab } from "../pages/SourcesPage";
import { AccessNote } from "./AccessNote";
import { GeneralTab } from "./GeneralTab";
import { SharingTab } from "./SharingTab";
import { SiteChips } from "../layout/SiteChips";
import { TABS } from "./tabs";
import { type SiteAccess, useSiteAccess } from "./useSiteAccess";

/** Tab content for the selected site. GeneralTab and SharingTab work out their own access. */
function TabContent({ tab, site, access }: { tab: string; site: Site; access: SiteAccess }) {
  switch (tab) {
    case "general":
      return <GeneralTab key={site.id} site={site} />;
    case "collectors":
      return <CollectorsTab key={site.id} siteId={site.id} readOnly={access.readOnly} />;
    case "tariffs":
      return <TariffsTab key={site.id} siteId={site.id} readOnly={access.readOnly} />;
    case "billing":
      return <BillingTab key={site.id} siteId={site.id} readOnly={access.readOnly} />;
    case "sharing":
      return <SharingTab key={site.id} site={site} />;
    default:
      return <div>Tab: {tab}</div>;
  }
}

/** The general tab shows its own note; sharing has rules of its own for viewers. */
const TABS_WITH_SHELL_NOTE = new Set(["collectors", "tariffs", "billing"]);

export function SettingsShell({ sites, site, tab }: { sites: Site[]; site: Site; tab: string }) {
  const access = useSiteAccess(site);
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 p-4">
      <div className="flex items-center gap-2">
        <BackLink to={`/sites/${site.id}`} />
        <h1 className="font-display text-2xl font-semibold text-ink">Site settings</h1>
      </div>
      <SiteChips sites={sites} selectedId={site.id} hrefFor={(id) => `/settings/sites/${id}/${tab}`} />
      <div className="flex flex-col gap-4 sm:flex-row">
        <SideRail
          label="Site settings"
          items={TABS.map(({ id, label }) => ({ to: `/settings/sites/${site.id}/${id}`, label }))}
        />
        <div data-testid="settings-tab" className="min-w-0 flex-1 space-y-4">
          {TABS_WITH_SHELL_NOTE.has(tab) && <AccessNote note={access.note} />}
          <TabContent tab={tab} site={site} access={access} />
        </div>
      </div>
    </div>
  );
}
