import { useParams } from "react-router-dom";
import { QueryError } from "../components/QueryError";
import { BackLink } from "../layout/BackLink";
import { AdministratorSection } from "./AdministratorSection";
import { DetailsSection } from "./DetailsSection";
import { SecuritySection } from "./SecuritySection";
import { SiteAccessSection } from "./SiteAccessSection";
import { useAdminUser, useAdminUsers } from "./useAdminUsers";

export function UserDetailPage() {
  const { userId } = useParams<{ userId: string }>();
  const id = Number(userId);
  const userQuery = useAdminUser(id);
  const listQuery = useAdminUsers();
  const user = userQuery.data;
  const allUsers = listQuery.data ?? [];

  return (
    <div className="mx-auto max-w-2xl space-y-6 px-4 py-8">
      <div className="flex items-center gap-2">
        <BackLink to="/admin/users" />
        <h1 className="font-display text-2xl font-semibold text-ink">{user?.display_name ?? user?.email ?? "User"}</h1>
      </div>
      {userQuery.isError && !user && (
        <QueryError error={userQuery.error} fallback="Could not load this user." onRetry={() => void userQuery.refetch()} />
      )}
      {user && (
        <>
          <DetailsSection user={user} />
          <SiteAccessSection user={user} allUsers={allUsers} />
          <SecuritySection user={user} />
          <AdministratorSection user={user} allUsers={allUsers} />
        </>
      )}
    </div>
  );
}
