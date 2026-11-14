import type { ActorFailure, AlertSeverity } from "./types";

export type Tone = AlertSeverity | "neutral";

const ORDER: AlertSeverity[] = ["failure", "partial", "notice"];

/** The tile colour: the worst severity among rows that are not cleared. */
export function tileTone(failures: ActorFailure[]): Tone {
  const open = failures.filter((f) => f.cleared_at === null);
  return ORDER.find((s) => open.some((f) => f.severity === s)) ?? "neutral";
}

/** needs_sign_in has no real start time (the server sends "now"), so it counts as oldest. */
const sinceMs = (f: ActorFailure) => (f.kind === "needs_sign_in" ? 0 : new Date(f.since).getTime() || 0);

/** Unresolved rows first, by severity and then newest first; cleared rows come last. */
export function orderFailures(failures: ActorFailure[]): ActorFailure[] {
  const rank = (f: ActorFailure) => (f.cleared_at === null ? 0 : ORDER.length) + ORDER.indexOf(f.severity);
  return [...failures].sort(
    (a, b) => rank(a) - rank(b) || sinceMs(b) - sinceMs(a) || (a.instance_id ?? 0) - (b.instance_id ?? 0),
  );
}
