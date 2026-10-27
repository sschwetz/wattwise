import { Link } from "react-router-dom";
import { errorMessage } from "../api/client";
import { usePendingInvites } from "../sites/useInvites";

export function SettingsError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div className="flex flex-col items-start gap-2 p-4 text-sm text-ink">
      <p role="alert">{errorMessage(error, "Could not load your sites.")}</p>
      <button type="button" onClick={onRetry} className="rounded-md border border-line px-3 py-1 hover:bg-surface-2">
        Retry
      </button>
      <Link to="/sites" className="text-solar hover:underline">
        Back to your sites
      </Link>
    </div>
  );
}

export function SettingsEmpty() {
  const { data: pending } = usePendingInvites();
  return (
    <div className="flex flex-col items-start gap-2 p-4 text-sm text-ink">
      <p>You have no sites yet.</p>
      <Link to="/sites/new" className="text-solar hover:underline">
        Add a site
      </Link>
      {pending && pending.length > 0 && (
        <Link to="/sites" className="text-solar hover:underline">
          View invitations
        </Link>
      )}
    </div>
  );
}
