import type { ActorFailure, AlertSeverity } from "./types";

export type Tone = AlertSeverity | "neutral";

const ORDER: AlertSeverity[] = ["failure", "partial", "notice"];

/** The tile colour: the worst severity among rows that are not cleared. */
export function tileTone(failures: ActorFailure[]): Tone {
  const open = failures.filter((f) => f.cleared_at === null);
  return ORDER.find((s) => open.some((f) => f.severity === s)) ?? "neutral";
}

const sinceMs = (f: ActorFailure) => new Date(f.since).getTime();

/** Unresolved rows first, by severity and then newest first; cleared rows come last. */
export function orderFailures(failures: ActorFailure[]): ActorFailure[] {
  const rank = (f: ActorFailure) => (f.cleared_at === null ? 0 : ORDER.length) + ORDER.indexOf(f.severity);
  return [...failures].sort((a, b) => rank(a) - rank(b) || sinceMs(b) - sinceMs(a));
}
