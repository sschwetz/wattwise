import { Link } from "react-router-dom";
import { useSites } from "../sites/useSites";
import { formatRunTime } from "./runFormat";
import { useNow } from "../dashboard/useNow";
import { hintClass } from "./styles";
import type { ActorFailure, ActorFailureKind } from "./types";
import { useActorFailures, useActorInstances } from "./useActors";

const MAX_SHOWN = 5;
const CLOCK_MS = 60_000;

const KIND_LABEL: Record<ActorFailureKind, string> = {
  run_failed: "Run failed",
  still_off: "Still off: the unit may still be off",
  actor_silent: "Actor not reporting: the device program stopped reporting",
  needs_sign_in: "Needs sign-in",
  source_alert: "Source problem",
};

const linkClass = "text-sm font-medium text-ink underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60";
const tileClass = "rounded-xl border border-line bg-surface p-4";

function oldestFirst(failures: ActorFailure[]): ActorFailure[] {
  return [...failures].sort((a, b) => new Date(a.since).getTime() - new Date(b.since).getTime());
}

/** needs_sign_in has no real start time: the server sends "now" on every request, so no time is shown. */
function FailureRow({ failure, timezone }: { failure: ActorFailure; timezone: string | undefined }) {
  const now = useNow(CLOCK_MS);
  return (
    <li className="space-y-0.5 border-t border-line pt-2 text-sm first:border-t-0 first:pt-0">
      <p className="font-medium text-danger">{KIND_LABEL[failure.kind] ?? failure.kind}</p>
      {failure.schedule_name && <p className="text-ink">{failure.schedule_name}</p>}
      <p className="text-ink">{failure.message}</p>
      {timezone && failure.kind !== "needs_sign_in" && (
        <time dateTime={failure.since} className="block text-ink-muted">
          {formatRunTime(failure.since, timezone, now)}
        </time>
      )}
    </li>
  );
}

const scheduleHref = (siteId: number) => `/settings/sites/${siteId}/schedule`;
const collectorsHref = (siteId: number) => `/settings/sites/${siteId}/collectors`;

function FailureList({ failures, siteId }: { failures: ActorFailure[]; siteId: number }) {
  const timezone = useSites().data?.find((s) => s.id === siteId)?.timezone;
  const shown = oldestFirst(failures).slice(0, MAX_SHOWN);
  const hidden = failures.length - shown.length;
  const onlySourceAlerts = failures.every((f) => f.kind === "source_alert");
  const href = onlySourceAlerts ? collectorsHref(siteId) : scheduleHref(siteId);
  return (
    <>
      <h2 className="font-display text-lg font-semibold text-ink">Needs attention ({failures.length})</h2>
      <ul className="mt-3 space-y-2">
        {shown.map((f) => (
          <FailureRow key={`${f.kind}-${f.instance_id}-${f.schedule_id}-${f.schedule_name}`} failure={f} timezone={timezone} />
        ))}
      </ul>
      {hidden > 0 && <Link to={href} className={`${linkClass} mt-2 block`}>and {hidden} more</Link>}
      <Link to={href} className={`${linkClass} mt-3 block`}>
        {onlySourceAlerts ? "Open the Collectors tab" : "Open the Schedule tab"}
      </Link>
    </>
  );
}

/** Dashboard summary of current schedule and source problems. Without an actor it shows source problems only. */
export function FailuresTile({ siteId }: { siteId: number }) {
  const instances = useActorInstances(siteId);
  const failures = useActorFailures(siteId);
  if (!instances.data) return null;
  const hasActors = instances.data.length > 0;
  const href = scheduleHref(siteId);
  // A failed background refetch keeps the failures already shown.
  if (failures.isError && !failures.data) return hasActors ? <p className={hintClass}>Schedule status unavailable</p> : null;
  if (!failures.data) return null;
  const current = hasActors ? failures.data : failures.data.filter((f) => f.kind === "source_alert");
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
    <section aria-label="Needs attention" className={`${tileClass} danger-glow`}>
      <div role="status" aria-live="polite">
        <FailureList failures={current} siteId={siteId} />
      </div>
    </section>
  );
}
