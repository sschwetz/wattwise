import { type FormEvent, useState } from "react";
import { useMe } from "../account/useAccount";
import { errorMessage } from "../api/client";
import { formatDay, localDate } from "../billing/dates";
import { type Invite, type InviteStatus, useCreateInvite, useRevokeInvite, useSiteInvites } from "../sites/useInvites";
import type { Site } from "../sites/useSites";

const inputClass =
  "w-full rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50";
const buttonClass =
  "rounded-md px-3 py-1.5 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50";

const STATUS_LABEL: Record<InviteStatus, string> = {
  invited: "Invited",
  accepted: "Accepted",
  declined: "Declined",
  revoked: "Revoked",
};

function statusInstant(invite: Invite): string {
  const changed = {
    invited: invite.created_at,
    accepted: invite.accepted_at,
    declined: invite.declined_at,
    revoked: invite.revoked_at,
  }[invite.status];
  return changed ?? invite.created_at;
}

function InviteForm({ siteId }: { siteId: number }) {
  const [email, setEmail] = useState("");
  const create = useCreateInvite(siteId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    create.mutate({ email: email.trim() }, { onSuccess: () => setEmail("") });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2">
      <label className="block space-y-1 text-sm text-ink">
        Email
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="off"
          required
          className={inputClass}
        />
      </label>
      {create.isError && (
        <p role="alert" className="text-sm text-danger">
          {errorMessage(create.error, "Could not send the invitation. Please try again.")}
        </p>
      )}
      {create.isSuccess && (
        <p role="status" className="text-sm text-ink-muted">
          Invitation sent
        </p>
      )}
      <button type="submit" disabled={create.isPending} className={`${buttonClass} bg-solar text-[#1a1203]`}>
        Send invite
      </button>
    </form>
  );
}

function RevokeControl({ siteId, inviteId }: { siteId: number; inviteId: number }) {
  const [confirming, setConfirming] = useState(false);
  const revoke = useRevokeInvite(siteId);

  return (
    <div className="space-y-1">
      {confirming ? (
        <div className="flex items-center gap-2">
          <span className="text-sm text-ink">Remove access?</span>
          <button type="button" onClick={() => setConfirming(false)} className={`${buttonClass} border border-line text-ink`}>
            Cancel
          </button>
          <button
            type="button"
            disabled={revoke.isPending}
            onClick={() => revoke.mutate(inviteId, { onSettled: () => setConfirming(false) })}
            className={`${buttonClass} bg-danger text-white`}
          >
            Confirm revoke
          </button>
        </div>
      ) : (
        <button type="button" onClick={() => setConfirming(true)} className={`${buttonClass} border border-line text-ink`}>
          Revoke
        </button>
      )}
      {revoke.isError && (
        <p role="alert" className="text-sm text-danger">
          {errorMessage(revoke.error, "Could not revoke. Please try again.")}
        </p>
      )}
    </div>
  );
}

function canRevoke(status: InviteStatus, isOwner: boolean): boolean {
  return status === "accepted" || (status === "invited" && isOwner);
}

function InviteRow({ invite, isOwner, timeZone }: { invite: Invite; isOwner: boolean; timeZone: string }) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-2 rounded-md border border-line p-3">
      <div className="min-w-0">
        <p className="break-all text-sm text-ink">{invite.email}</p>
        <p className="text-sm text-ink-muted">
          <span>{STATUS_LABEL[invite.status]}</span>
          {" · "}
          <span>{formatDay(localDate(statusInstant(invite), timeZone))}</span>
        </p>
      </div>
      {canRevoke(invite.status, isOwner) && <RevokeControl siteId={invite.site_id} inviteId={invite.id} />}
    </li>
  );
}

function InviteList({ site, isOwner }: { site: Site; isOwner: boolean }) {
  const invites = useSiteInvites(site.id);
  if (invites.isPending) return <p className="text-sm text-ink-muted">Loading invitations...</p>;
  if (invites.isError) {
    return (
      <div className="flex flex-col items-start gap-2 text-sm text-ink">
        <p role="alert">{errorMessage(invites.error, "Could not load invitations.")}</p>
        <button type="button" onClick={() => invites.refetch()} className="rounded-md border border-line px-3 py-1 hover:bg-surface-2">
          Retry
        </button>
      </div>
    );
  }
  if (invites.data.length === 0) return <p className="text-sm text-ink-muted">No invitations yet.</p>;
  return (
    <ul className="space-y-2">
      {invites.data.map((invite) => (
        <InviteRow key={invite.id} invite={invite} isOwner={isOwner} timeZone={site.timezone} />
      ))}
    </ul>
  );
}

export function SharingTab({ site }: { site: Site }) {
  const me = useMe();
  const isOwner = me.isSuccess && me.data.id === site.owner_user_id;

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-medium text-ink">Invitations</h2>
      {me.isError && (
        <p className="text-sm text-ink-muted">Your account details could not be loaded, so sharing is read-only.</p>
      )}
      {isOwner && <InviteForm siteId={site.id} />}
      <InviteList site={site} isOwner={isOwner} />
    </div>
  );
}
