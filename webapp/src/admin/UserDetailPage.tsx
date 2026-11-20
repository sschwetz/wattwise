import { useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { QueryError } from "../components/QueryError";
import { BackLink } from "../layout/BackLink";
import { AdministratorSection } from "./AdministratorSection";
import { DetailsSection } from "./DetailsSection";
import { SecuritySection } from "./SecuritySection";
import { SiteAccessSection } from "./SiteAccessSection";
import { mutedClass } from "./adminUi";
import { isUserId, useAdminUser, useAdminUsers, type AdminUser } from "./useAdminUsers";

function ListStatus({ listQuery }: { listQuery: ReturnType<typeof useAdminUsers> }) {
  if (listQuery.isError) {
    return <QueryError error={listQuery.error} fallback="Could not load the user list." onRetry={() => void listQuery.refetch()} />;
  }
  return <p className={mutedClass}>Loading...</p>;
}

/**
 * Site access and the administrator toggle read the full user list. Security
 * does not, so it renders in the same position whether or not the list loaded.
 */
function UserSections({ user }: { user: AdminUser }) {
  const listQuery = useAdminUsers();
  const allUsers = listQuery.data;
  const security = <SecuritySection user={user} />;
  if (!allUsers) {
    return (
      <>
        <ListStatus listQuery={listQuery} />
        {security}
      </>
    );
  }
  return (
    <>
      <SiteAccessSection user={user} allUsers={allUsers} />
      {security}
      <AdministratorSection user={user} allUsers={allUsers} />
    </>
  );
}

function UserBody({ id }: { id: number }) {
  const userQuery = useAdminUser(id);
  const user = userQuery.data;
  if (user) {
    return (
      <>
        <DetailsSection user={user} />
        <UserSections user={user} />
      </>
    );
  }
  if (userQuery.error instanceof ApiError && userQuery.error.status === 404) return <p className={mutedClass}>User not found</p>;
  if (userQuery.isError) {
    return <QueryError error={userQuery.error} fallback="Could not load this user." onRetry={() => void userQuery.refetch()} />;
  }
  return <p className={mutedClass}>Loading...</p>;
}

export function UserDetailPage() {
  const { userId } = useParams<{ userId: string }>();
  const id = Number(userId);
  const { data: user } = useAdminUser(id);

  return (
    <div className="mx-auto max-w-2xl space-y-6 px-4 py-8">
      <div className="flex items-center gap-2">
        <BackLink to="/admin/users" />
        <h1 className="font-display text-2xl font-semibold text-ink">{user?.display_name ?? user?.email ?? "User"}</h1>
      </div>
      {isUserId(id) ? <UserBody id={id} /> : <p className={mutedClass}>User not found</p>}
    </div>
  );
}
