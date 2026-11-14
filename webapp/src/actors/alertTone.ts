import type { ActorFailure, AlertSeverity } from "./types";

export type Tone = AlertSeverity | "neutral";

const ORDER: AlertSeverity[] = ["failure", "partial", "notice"];

/** The tile colour: the worst severity among rows that are not cleared. */
export function tileTone(failures: ActorFailure[]): Tone {
  const open = failures.filter((f) => f.cleared_at === null);
  return ORDER.find((s) => open.some((f) => f.severity === s)) ?? "neutral";
}
