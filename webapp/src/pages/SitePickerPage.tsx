import { Link, Navigate } from "react-router-dom";
import { AuthCard } from "../layout/AuthCard";
import { SiteForm } from "../sites/SiteForm";
import { useSites } from "../sites/useSites";

export function SitePickerPage() {
  const { data: sites, isLoading } = useSites();

  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center bg-bg">
        <p className="text-ink-muted">Loading your sites…</p>
      </div>
    );
  }
  if (!sites || sites.length === 0) {
    return <SiteForm title="Create your first site" />;
  }
  if (sites.length === 1) {
    return <Navigate to={`/sites/${sites[0].id}`} replace />;
  }
  return (
    <AuthCard title="Choose a site" fill>
      <ul className="space-y-2">
        {sites.map((site) => (
          <li key={site.id}>
            <Link
              to={`/sites/${site.id}`}
              className="block rounded-md border border-line px-3 py-2 text-ink hover:border-solar hover:bg-surface-2"
            >
              {site.name}
            </Link>
          </li>
        ))}
      </ul>
    </AuthCard>
  );
}
