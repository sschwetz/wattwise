import { Link, Outlet, useLocation, useParams } from "react-router-dom";
import { useSites } from "../sites/useSites";
import { SiteChips } from "./SiteChips";
import { SideRail } from "./SideRail";

/**
 * Shared chrome for the site-scoped pages, laid out like Site settings: site
 * chips across the top, a left rail between Dashboard/Reporting, then the page.
 * Child routes inherit :siteId from here.
 */
export function SiteLayout() {
  const { siteId } = useParams<{ siteId: string }>();
  const { pathname } = useLocation();
  const { data: sites } = useSites();
  const disabled = sites?.find((s) => String(s.id) === siteId)?.enabled === false;
  // Picking another site stays on the same page (Dashboard, Reporting, Help).
  const section = pathname.slice(`/sites/${siteId}`.length);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-4 p-4">
      {sites && (
        <SiteChips sites={sites} selectedId={Number(siteId)} hrefFor={(id) => `/sites/${id}${section}`} />
      )}
      {disabled && (
        <div role="status" className="rounded-md border border-line bg-surface-2 px-4 py-2 text-sm text-ink">
          This site is disabled and not collecting data.{" "}
          <Link to={`/settings/sites/${siteId}/general`} className="text-solar underline">
            Open settings
          </Link>
        </div>
      )}
      <div className="flex flex-col gap-4 sm:flex-row">
        <SideRail
          label="Site"
          items={[
            { to: `/sites/${siteId}`, label: "Dashboard", end: true },
            { to: `/sites/${siteId}/reporting`, label: "Reporting" },
          ]}
        />
        <main className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
