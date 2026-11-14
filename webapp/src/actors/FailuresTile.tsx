import { Link } from "react-router-dom";
import { useSites } from "../sites/useSites";
import { Bulb } from "./Bulb";
import { orderFailures, tileTone, type Tone } from "./alertTone";
import { formatAlertTime } from "./runFormat";
import { hintClass } from "./styles";
import type { ActorFailure } from "./types";
import { useActorFailures, useActorInstances, useDismissAlert } from "./useActors";

const MAX_SHOWN = 5;

const GLOW: Record<Tone, string> = {
  failure: " danger-glow",
  partial: " partial-glow",
  notice: " notice-glow",
  neutral: "",
};

const SHORT_MESSAGE: Partial<Record<ActorFailure["kind"], string>> = {
  still_off: "Still off",
  actor_silent: "Not reporting",
  needs_sign_in: "Needs sign-in",
  run_failed: "Run failed",
};

const linkClass = "text-sm font-medium text-ink underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60";
const buttonClass = "shrink-0 rounded-md border border-line px-2 py-0.5 text-sm font-medium text-ink hover:bg-surface-2 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60";
const tileClass = "rounded-xl border border-line bg-surface p-4";

const scheduleHref = (siteId: number) => `/settings/sites/${siteId}/schedule`;
const collectorsHref = (siteId: number) => `/settings/sites/${siteId}/collectors`;

function labelled(failure: ActorFailure, message: string): string {
  const label = failure.kind === "alert" ? failure.subject : failure.schedule_name;
  return label ? `${label} - ${message}` : message;
}

function rowText(failure: ActorFailure): string {
  return labelled(failure, SHORT_MESSAGE[failure.kind] ?? failure.message);
}

function DismissButton({ siteId, alertId, text }: { siteId: number; alertId: number | null; text: string }) {
  const dismiss = useDismissAlert(siteId);
  return (
    <button type="button" aria-label={`Dismiss: ${text}`} className={buttonClass} disabled={dismiss.isPending || alertId === null} onClick={() => alertId !== null && dismiss.mutate(alertId)}>
      Dismiss
    </button>
  );
}

function RowAction({ failure, siteId }: { failure: ActorFailure; siteId: number }) {
  if (failure.sticky) return <DismissButton siteId={siteId} alertId={failure.alert_id} text={rowText(failure)} />;
  const isAlert = failure.kind === "alert";
  return <Link to={isAlert ? collectorsHref(siteId) : scheduleHref(siteId)} className={linkClass}>{isAlert ? "Open Collectors" : "Open Schedule"}</Link>;
}

/** needs_sign_in has no real start time: the server sends "now" on every request, so no time is shown. */
function FailureRow({ failure, siteId, timezone }: { failure: ActorFailure; siteId: number; timezone: string | undefined }) {
  const showTime = timezone && failure.kind !== "needs_sign_in";
  return (
    <li className="flex items-center gap-2 border-t border-line pt-2 text-sm first:border-t-0 first:pt-0">
      <Bulb severity={failure.severity} cleared={failure.cleared_at !== null} />
      {showTime && <time dateTime={failure.since} className="shrink-0 text-ink-muted">{formatAlertTime(failure.since, timezone)}</time>}
      <span className="min-w-0 flex-1 truncate text-ink" title={labelled(failure, failure.message)}>{rowText(failure)}</span>
      <RowAction failure={failure} siteId={siteId} />
    </li>
  );
}

const rowKey = (f: ActorFailure) => (f.alert_id !== null ? `alert-${f.alert_id}` : `${f.kind}-${f.instance_id}-${f.schedule_id}`);

function FailureList({ failures, siteId }: { failures: ActorFailure[]; siteId: number }) {
  const timezone = useSites().data?.find((s) => s.id === siteId)?.timezone;
  const ordered = orderFailures(failures);
  const shown = ordered.slice(0, MAX_SHOWN);
  const hidden = ordered.length - shown.length;
  const open = failures.filter((f) => f.cleared_at === null).length;
  const hasSchedule = failures.some((f) => f.kind !== "alert");
  return (
    <>
      <h2 className="font-display text-lg font-semibold text-ink">{open > 0 ? `Needs attention (${open})` : "Needs attention"}</h2>
      <ul className="mt-3 space-y-2">
        {shown.map((f) => <FailureRow key={rowKey(f)} failure={f} siteId={siteId} timezone={timezone} />)}
      </ul>
      {hidden > 0 && <Link to={hasSchedule ? scheduleHref(siteId) : collectorsHref(siteId)} className={`${linkClass} mt-2 block`}>and {hidden} more</Link>}
    </>
  );
}

/** Dashboard summary of current schedule failures and alerts. Without an actor it shows alerts only. */
export function FailuresTile({ siteId }: { siteId: number }) {
  const instances = useActorInstances(siteId);
  const failures = useActorFailures(siteId);
  if (!instances.data) return null;
  const hasActors = instances.data.length > 0;
  const href = scheduleHref(siteId);
  // A failed background refetch keeps the failures already shown.
  if (failures.isError && !failures.data) return hasActors ? <p className={hintClass}>Schedule status unavailable</p> : null;
  if (!failures.data) return null;
  const current = hasActors ? failures.data : failures.data.filter((f) => f.kind === "alert");
  if (current.length === 0) {
    if (!hasActors) return null;
    return (
      <section aria-label="Schedule status" className={tileClass}>
        <div role="status" aria-live="polite" className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-sm text-ink-muted">All schedules OK</p>
          <Link to={href} className={linkClass}>Open the Schedule tab</Link>
        </div>
      </section>
    );
  }
  return (
    <section aria-label="Needs attention" className={`${tileClass}${GLOW[tileTone(current)]}`}>
      <div role="status" aria-live="polite">
        <FailureList failures={current} siteId={siteId} />
      </div>
    </section>
  );
}
