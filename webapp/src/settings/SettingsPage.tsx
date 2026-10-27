import { Navigate, useParams } from "react-router-dom";
import { useSites, type Site } from "../sites/useSites";
import { SettingsShell } from "./SettingsShell";
import { SettingsEmpty, SettingsError } from "./SettingsStates";
import { TABS } from "./tabs";

function defaultSite(sites: Site[]): Site {
  return sites.find((site) => site.enabled) ?? sites[0];
}

/** The URL's site and tab are only trusted when they exist; otherwise redirect to ones that do. */
function resolveTarget(sites: Site[], siteId?: string, tab?: string): { site: Site; tab: string; valid: boolean } {
  const found = sites.find((site) => String(site.id) === siteId);
  const tabValid = TABS.some((t) => t.id === tab);
  const site = found ?? defaultSite(sites);
  return { site, tab: tabValid ? (tab as string) : "general", valid: found !== undefined && tabValid };
}

export function SettingsPage() {
  const { siteId, tab } = useParams<{ siteId: string; tab: string }>();
  const { data: sites, isLoading, isError, error, refetch } = useSites();

  if (isError) return <SettingsError error={error} onRetry={() => void refetch()} />;
  if (isLoading || !sites) return <p className="p-4 text-sm text-ink-muted">Loading...</p>;
  if (sites.length === 0) return <SettingsEmpty />;

  const target = resolveTarget(sites, siteId, tab);
  if (!target.valid) {
    return <Navigate to={`/settings/sites/${target.site.id}/${target.tab}`} replace />;
  }
  return <SettingsShell sites={sites} siteId={target.site.id} tab={target.tab} />;
}
