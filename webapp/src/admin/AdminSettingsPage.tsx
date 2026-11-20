import { Navigate, useParams } from "react-router-dom";
import { SideRail } from "../layout/SideRail";
import { ADMIN_TABS } from "./tabs";
import { UsersTab } from "./UsersTab";

export function AdminSettingsPage() {
  const { tab } = useParams<{ tab: string }>();

  // No tab, or one that does not exist, opens the first tab, as the site settings page does.
  if (!ADMIN_TABS.some((t) => t.id === tab)) {
    return <Navigate to={`/admin/${ADMIN_TABS[0].id}`} replace />;
  }
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 p-4">
      <h1 className="font-display text-2xl font-semibold text-ink">Admin Settings</h1>
      <div className="flex flex-col gap-4 sm:flex-row">
        <SideRail
          label="Admin settings"
          items={ADMIN_TABS.map(({ id, label }) => ({ to: `/admin/${id}`, label }))}
        />
        <div data-testid="admin-tab" className="min-w-0 flex-1 space-y-4">
          {tab === "users" && <UsersTab />}
        </div>
      </div>
    </div>
  );
}
