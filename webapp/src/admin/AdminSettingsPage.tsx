import { Navigate, useParams } from "react-router-dom";
import { SideRail } from "../layout/SideRail";
import { ADMIN_TABS } from "./tabs";

export function AdminSettingsPage() {
  const { tab } = useParams<{ tab: string }>();

  // With no tab in the URL, open the first one. There is nothing to open while the list is empty.
  if (tab === undefined && ADMIN_TABS.length > 0) {
    return <Navigate to={`/admin/${ADMIN_TABS[0].id}`} replace />;
  }
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 p-4">
      <h1 className="font-display text-2xl font-semibold text-ink">Admin Settings</h1>
      {ADMIN_TABS.length > 0 && (
        <div className="flex flex-col gap-4 sm:flex-row">
          <SideRail
            label="Admin settings"
            items={ADMIN_TABS.map(({ id, label }) => ({ to: `/admin/${id}`, label }))}
          />
          <div data-testid="admin-tab" className="min-w-0 flex-1 space-y-4" />
        </div>
      )}
    </div>
  );
}
