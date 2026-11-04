import { useNow } from "../dashboard/useNow";
import { useResetSignIn, useUpdateInstance } from "./useActors";
import { errorMessage } from "../api/client";
import { cardClass, noticeClass, primaryButtonClass, buttonClass } from "./styles";
import type { ActorInstance, AuthState } from "./types";

type CodeState = Extract<AuthState, { user_code: string }>;

export interface InstanceNoticeProps {
  siteId: number;
  instance: ActorInstance;
  timezone: string;
  canEdit: boolean;
}

function formatExpiry(iso: string, timezone: string): string {
  return new Intl.DateTimeFormat("en-AU", { timeStyle: "short", timeZone: timezone }).format(new Date(iso));
}

function ExpiryLine({ expiresAt, timezone }: { expiresAt: string; timezone: string }) {
  const now = useNow(15_000);
  if (new Date(expiresAt).getTime() <= now.getTime()) {
    return <p className="text-sm text-ink-muted">This code has expired. A new one will appear shortly.</p>;
  }
  return (
    <p className="text-sm text-ink-muted">
      The code expires at {formatExpiry(expiresAt, timezone)}. This page updates once the sign-in is approved.
    </p>
  );
}

export function SignInPanel({ auth, timezone }: { auth: CodeState; timezone: string }) {
  return (
    <section className={`${cardClass} space-y-3`}>
      <h2 className="text-lg font-medium text-ink">Sign in to Actron</h2>
      <p className="text-sm text-ink-muted">Enter this code on the Actron sign-in page:</p>
      <p className="font-mono text-3xl tracking-widest text-ink">{auth.user_code}</p>
      <a href={auth.verification_uri} target="_blank" rel="noopener noreferrer" className="text-solar underline">
        Open the Actron sign-in page
      </a>
      <ExpiryLine expiresAt={auth.expires_at} timezone={timezone} />
    </section>
  );
}

export function NeedsSignInNotice({ siteId, instance, canEdit }: Omit<InstanceNoticeProps, "timezone">) {
  const reset = useResetSignIn(siteId);
  return (
    <div role="status" className={`${noticeClass} flex flex-wrap items-center gap-3`}>
      <span>The Actron account needs to sign in again.</span>
      {canEdit && (
        <button
          type="button"
          disabled={reset.isPending}
          onClick={() => reset.mutate({ instanceId: instance.id })}
          className={`${buttonClass} border border-danger/30`}
        >
          Sign in again
        </button>
      )}
      {reset.isError && <span role="alert">{errorMessage(reset.error, "Could not start a new sign-in.")}</span>}
    </div>
  );
}

export function DisabledNotice({ siteId, instance, canEdit }: Omit<InstanceNoticeProps, "timezone">) {
  const update = useUpdateInstance(siteId);
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm text-ink-muted">
      <span>This device is turned off, so no schedules run.</span>
      {canEdit && (
        <button
          type="button"
          disabled={update.isPending}
          onClick={() => update.mutate({ id: instance.id, enabled: true })}
          className={primaryButtonClass}
        >
          Turn on
        </button>
      )}
      {update.isError && (
        <span role="alert" className="text-danger">
          {errorMessage(update.error, "Could not turn the device on.")}
        </span>
      )}
    </div>
  );
}
