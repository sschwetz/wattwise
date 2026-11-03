import { Link, Navigate } from "react-router-dom";
import { errorMessage } from "../api/client";
import { AuthCard } from "../layout/AuthCard";
import { SiteForm } from "../sites/SiteForm";
import { type PendingInvite, useAcceptInvite, useDeclineInvite, usePendingInvites } from "../sites/useInvites";
import { useEnabledSites, useSites } from "../sites/useSites";

function PendingInvitations({ invites }: { invites: PendingInvite[] }) {
  const accept = useAcceptInvite();
  const decline = useDeclineInvite();
  const error = accept.error ?? decline.error;
  const busy = accept.isPending || decline.isPending;

  return (
    <section className="mb-4 space-y-2">
      <h2 className="text-sm font-medium text-ink">Pending invitations</h2>
      {error && (
        <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
          {errorMessage(error, "Could not update the invitation. Please try again.")}
        </p>
      )}
      <ul className="space-y-2">
        {invites.map((invite) => (
          <li key={invite.id} className="rounded-md border border-line px-3 py-2">
            <p className="text-ink">{invite.site_name}</p>
            <p className="text-sm text-ink-muted">Invited by {invite.inviter_email}</p>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => accept.mutate(invite.id)}
                className="rounded-md bg-solar px-3 py-1 text-sm font-medium text-[#1a1203] hover:bg-solar/90 disabled:opacity-50"
              >
                Accept
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => decline.mutate(invite.id)}
                className="rounded-md border border-line px-3 py-1 text-sm text-ink hover:bg-surface-2 disabled:opacity-50"
              >
                Decline
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function SiteList({ sites }: { sites: { id: number; name: string }[] }) {
  return (
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
  );
}

function LoadingSites() {
  return (
    <div className="flex flex-1 items-center justify-center bg-bg">
      <p className="text-ink-muted">Loading your sites…</p>
    </div>
  );
}

function LoadFailed({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <AuthCard title="Could not load your sites" fill>
      <p role="alert" className="text-sm text-danger">
        {errorMessage(error, "Something went wrong. Please try again.")}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-4 rounded-md border border-line px-3 py-1 text-sm text-ink hover:bg-surface-2"
      >
        Retry
      </button>
    </AuthCard>
  );
}

export function SitePickerPage() {
  const sitesQuery = useSites();
  const { data: allSites, isLoading: sitesLoading } = sitesQuery;
  const { data: sites = [] } = useEnabledSites();
  const pendingQuery = usePendingInvites();
  const { data: pending, isLoading: pendingLoading } = pendingQuery;
  const invites = pending ?? [];

  if (sitesLoading || pendingLoading) return <LoadingSites />;
  // Without a successful response we cannot tell "no sites" from "API down", so never fall through to onboarding.
  const failed = !allSites ? sitesQuery : !pending && allSites.length === 0 ? pendingQuery : undefined;
  if (failed) {
    const retry = () => void Promise.all([sitesQuery.refetch(), pendingQuery.refetch()]);
    return <LoadFailed error={failed.error} onRetry={retry} />;
  }
  const hasInvites = invites.length > 0;
  if (!hasInvites && sites.length === 1) {
    return <Navigate to={`/sites/${sites[0].id}`} replace />;
  }
  if (!hasInvites && allSites && allSites.length > 0 && sites.length === 0) {
    return <Navigate to="/settings" replace />;
  }
  if (!allSites || allSites.length === 0) {
    if (!hasInvites) return <SiteForm title="Create your first site" />;
    return (
      <AuthCard title="Welcome to WattWise" fill>
        <PendingInvitations invites={invites} />
        <Link to="/sites/new" className="mt-4 block text-sm text-solar hover:underline">
          Create your own site
        </Link>
      </AuthCard>
    );
  }
  return (
    <AuthCard title="Choose a site" fill>
      {hasInvites && <PendingInvitations invites={invites} />}
      <SiteList sites={sites} />
      {sites.length === 0 && (
        <Link to="/settings" className="mt-4 block text-sm text-solar hover:underline">
          Go to settings
        </Link>
      )}
    </AuthCard>
  );
}
