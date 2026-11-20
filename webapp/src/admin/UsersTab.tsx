import { useState } from "react";
import { Link } from "react-router-dom";
import { useMe } from "../account/useAccount";
import { useAvatarImage } from "../account/useAvatarImage";
import { QueryError } from "../components/QueryError";
import { Avatar } from "../layout/Avatar";
import { useAdminUsers, type AdminUser, type AdminUserSite } from "./useAdminUsers";

const chipClass = "shrink-0 rounded-full border border-line px-3 py-1 text-sm text-ink-muted";
const cellClass = "px-2 py-2 align-top";

function matches(user: AdminUser, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (needle === "") return true;
  return user.email.toLowerCase().includes(needle) || (user.display_name ?? "").toLowerCase().includes(needle);
}

function Chips({ sites }: { sites: AdminUserSite[] }) {
  if (sites.length === 0) return <span className="text-ink-muted">None</span>;
  return (
    <ul className="flex flex-wrap gap-2">
      {sites.map((site) => (
        <li key={site.site_id} className={chipClass}>
          {site.site_name}
        </li>
      ))}
    </ul>
  );
}

function UserRow({ user, avatarUrl }: { user: AdminUser; avatarUrl: string | null }) {
  const manages = user.sites.filter((site) => site.role !== "viewer");
  const views = user.sites.filter((site) => site.role === "viewer");
  return (
    <tr className="border-t border-line">
      <td className={cellClass}>
        <Link to={`/admin/users/${user.id}`} className="flex items-center gap-3 hover:underline">
          <Avatar name={user.display_name} email={user.email} imageUrl={avatarUrl} />
          <span className="min-w-0">
            {user.display_name && <span className="block truncate font-medium text-ink">{user.display_name}</span>}
            <span className="block truncate text-sm text-ink-muted">{user.email}</span>
          </span>
          {user.is_admin && (
            <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs font-medium text-ink">Admin</span>
          )}
        </Link>
      </td>
      <td className={cellClass}>
        <Chips sites={manages} />
      </td>
      <td className={cellClass}>
        <Chips sites={views} />
      </td>
    </tr>
  );
}

export function UsersTab() {
  const { data: users, isLoading, isError, error, refetch } = useAdminUsers();
  const { data: me } = useMe();
  // The avatar endpoint serves the signed-in user's image only, so other users get initials.
  const myAvatarUrl = useAvatarImage(me?.has_avatar ?? false);
  const [query, setQuery] = useState("");

  if (isError && !users) {
    return <QueryError error={error} fallback="Could not load users." onRetry={() => void refetch()} />;
  }
  if (isLoading || !users) return <p className="text-sm text-ink-muted">Loading...</p>;

  const shown = users.filter((user) => matches(user, query));
  return (
    <div className="space-y-4">
      <input
        type="search"
        aria-label="Search users"
        placeholder="Search by name or email"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        className="w-full max-w-sm rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink"
      />
      {shown.length === 0 ? (
        <p className="text-sm text-ink-muted">No users match your search.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-ink-muted">
                <th className="px-2 py-2 font-medium">User</th>
                <th className="px-2 py-2 font-medium">Manages</th>
                <th className="px-2 py-2 font-medium">Views</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((user) => (
                <UserRow key={user.id} user={user} avatarUrl={user.id === me?.id ? myAvatarUrl : null} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
