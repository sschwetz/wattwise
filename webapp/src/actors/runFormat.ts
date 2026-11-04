import { useEffect, useState } from "react";
import type { ActorRun, RunOutcome } from "./types";

/** The actor reports about every 30 seconds; two minutes of silence means it stopped. */
export const STALE_MS = 120_000;

/** The current time, refreshed on an interval so "Today" and "Not updating" stay right. */
export function useNow(intervalMs: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function isStale(lastSeen: string | null, now: Date): boolean {
  return lastSeen === null || now.getTime() - new Date(lastSeen).getTime() > STALE_MS;
}

/** The calendar day in a zone as "YYYY-MM-DD". */
function dayKey(date: Date, timezone: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

function previousDayKey(key: string): string {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d - 1)).toISOString().slice(0, 10);
}

function clock(date: Date, timezone: string): string {
  return new Intl.DateTimeFormat("en-AU", { timeZone: timezone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
}

/** "Today 16:00", "Yesterday 16:00", or "Sun 1 Nov, 16:00", all in the given zone. */
export function formatRunTime(iso: string, timezone: string, now: Date): string {
  const date = new Date(iso);
  const day = dayKey(date, timezone);
  const today = dayKey(now, timezone);
  if (day === today) return `Today ${clock(date, timezone)}`;
  if (day === previousDayKey(today)) return `Yesterday ${clock(date, timezone)}`;
  const short = new Intl.DateTimeFormat("en-AU", { timeZone: timezone, weekday: "short", day: "numeric", month: "short" }).format(date);
  return `${short}, ${clock(date, timezone)}`;
}

export const OUTCOME_LABEL: Record<RunOutcome, string> = {
  ok: "OK",
  skipped: "Skipped",
  failed: "Failed",
  overridden: "You took over",
  pending: "Queued",
  running: "Running",
};

const NEUTRAL_TONE = "bg-surface-2 text-ink-muted";

/** The pill text for an outcome; an outcome this app does not know shows as sent. */
export function outcomeLabel(outcome: string): string {
  return (OUTCOME_LABEL as Record<string, string | undefined>)[outcome] ?? outcome;
}

export function outcomeTone(outcome: string): string {
  return (OUTCOME_TONE as Record<string, string | undefined>)[outcome] ?? NEUTRAL_TONE;
}

export const OUTCOME_TONE: Record<RunOutcome, string> = {
  ok: "bg-flow-soft text-flow",
  skipped: "bg-solar-soft text-solar",
  failed: "bg-danger-soft text-danger",
  overridden: "bg-surface-2 text-ink-muted",
  pending: "bg-surface-2 text-ink-muted",
  running: "bg-surface-2 text-ink-muted",
};

const DEFAULT_MESSAGE: Partial<Record<RunOutcome, string>> = {
  skipped: "Skipped",
  failed: "Failed",
  overridden: "Changed by you",
};

const KIND_LABEL: Record<string, string | undefined> = { restore: "Restored", test: "Test run" };

/** What happened in a run, in one line. */
export function runDescription(run: ActorRun, actionLabel: string): string {
  const base = run.kind === "action" ? actionLabel : KIND_LABEL[run.kind] ?? "Run";
  const detail = run.message || (DEFAULT_MESSAGE as Record<string, string | undefined>)[run.outcome];
  const tries = run.outcome === "failed" && run.attempts > 1 ? ` (after ${run.attempts} tries)` : "";
  return `${detail ? `${base}: ${detail}` : base}${tries}`;
}

export function hasActiveRun(runs: ActorRun[] | undefined): boolean {
  return (runs ?? []).some((r) => r.outcome === "pending" || r.outcome === "running");
}
