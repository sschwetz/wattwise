import { useState } from "react";
import { Link } from "react-router-dom";
import { useMe } from "../account/useAccount";
import { useAvatarImage } from "../account/useAvatarImage";
import { chipBase } from "../components/chipStyles";
import { inputClass } from "../components/formStyles";
import { QueryError } from "../components/QueryError";
import { Avatar } from "../layout/Avatar";
import { useAdminUsers, type AdminUser, type AdminUserSite } from "./useAdminUsers";

const chipClass = `${chipBase} border-line text-ink-muted`;
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

function UserCell({ user, avatarUrl }: { user: AdminUser; avatarUrl: string | null }) {
  return (
    <div className="flex items-center gap-3">
      <Link to={`/admin/users/${user.id}`} className="flex min-w-0 items-center gap-3 hover:underline">
        <Avatar name={user.display_name} email={user.email} imageUrl={avatarUrl} />
        <span className="min-w-0">
          {user.display_name && <span className="block truncate font-medium text-ink">{user.display_name}</span>}
          <span className="block truncate text-sm text-ink-muted">{user.email}</span>
        </span>
      </Link>
      {user.is_admin && (
        <span className="shrink-0 whitespace-nowrap rounded-full bg-surface-2 px-2 py-0.5 text-xs font-medium text-ink">
          Admin
        </span>
      )}
    </div>
  );
}

function UserRow({ user, avatarUrl }: { user: AdminUser; avatarUrl: string | null }) {
  return (
    <tr className="border-t border-line">
      <td className={cellClass}>
        <UserCell user={user} avatarUrl={avatarUrl} />
      </td>
      <td className={cellClass}>
        <Chips sites={user.sites.filter((site) => site.role !== "viewer")} />
      </td>
      <td className={cellClass}>
        <Chips sites={user.sites.filter((site) => site.role === "viewer")} />
      </td>
    </tr>
  );
}

function SearchInput({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <input
      type="search"
      aria-label="Search users"
      placeholder="Search by name or email"
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className={`${inputClass} max-w-sm text-sm`}
    />
  );
}

function UserTable({ users, meId, myAvatarUrl }: { users: AdminUser[]; meId: number | undefined; myAvatarUrl: string | null }) {
  return (
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
          {users.map((user) => (
            <UserRow key={user.id} user={user} avatarUrl={user.id === meId ? myAvatarUrl : null} />
          ))}
        </tbody>
      </table>
    </div>
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
      <SearchInput value={query} onChange={setQuery} />
      {shown.length === 0 ? (
        <p className="text-sm text-ink-muted">No users match your search.</p>
      ) : (
        <UserTable users={shown} meId={me?.id} myAvatarUrl={myAvatarUrl} />
      )}
    </div>
  );
}
