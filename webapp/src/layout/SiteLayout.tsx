import { NavLink, Outlet, useParams } from "react-router-dom";
import { SiteSwitcher } from "./SiteSwitcher";

const primaryLinkClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-2 text-sm font-medium transition-colors ${
    isActive ? "bg-surface-2 text-ink" : "text-ink-muted hover:bg-surface-2 hover:text-ink"
  }`;

/**
 * Shared chrome for the site-scoped pages: a persistent sidebar between
 * Dashboard/Reporting/Sources/Billing plus a dropdown to switch to (or add) a site.
 * Child routes inherit :siteId from here.
 */
export function SiteLayout() {
  const { siteId } = useParams<{ siteId: string }>();

  return (
    <div className="flex flex-1 flex-col bg-bg sm:flex-row">
      <nav className="flex shrink-0 flex-row items-center gap-1 overflow-x-auto border-b border-line bg-surface px-3 py-3 sm:w-56 sm:flex-col sm:items-stretch sm:gap-0.5 sm:border-b-0 sm:border-r sm:px-3 sm:py-4">
        <div className="sm:mb-2">
          <SiteSwitcher />
        </div>
        <NavLink to={`/sites/${siteId}`} end className={primaryLinkClass}>
          Dashboard
        </NavLink>
        <NavLink to={`/sites/${siteId}/reporting`} className={primaryLinkClass}>
          Reporting
        </NavLink>
        <NavLink to={`/sites/${siteId}/sources`} className={primaryLinkClass}>
          Sources
        </NavLink>
        <NavLink to={`/sites/${siteId}/billing`} className={primaryLinkClass}>
          Billing
        </NavLink>
      </nav>
      <main className="flex-1">
        <Outlet />
      </main>
    </div>
  );
}
