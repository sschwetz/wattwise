import { formatTime, hasEnded } from "../billing/billingUi";
import { type DemandWindow, type SiteTariff, useDemandWindows, useTariffs } from "../billing/useBilling";
import { SHORT_DAYS, SHORT_MONTHS } from "../sources/rateWindowUi";

/** The tariff in force: the one with no end date, or else the one that started last. */
export function currentTariff(tariffs: SiteTariff[]): SiteTariff | undefined {
  const open = tariffs.find((t) => t.effective_to === null);
  if (open) return open;
  return [...tariffs].sort((a, b) => b.effective_from.localeCompare(a.effective_from))[0];
}

/** Consecutive values as "A-B" runs, joined by commas. A run may wrap from the last value to the first (Nov-Mar). */
export function formatRuns(values: number[], names: string[], first: number): string {
  const sorted = [...new Set(values)].sort((a, b) => a - b);
  if (sorted.length === names.length) return "";
  const runs: number[][] = [];
  for (const v of sorted) {
    const run = runs[runs.length - 1];
    if (run && v === run[run.length - 1] + 1) run.push(v);
    else runs.push([v]);
  }
  const last = first + names.length - 1;
  if (runs.length > 1 && runs[0][0] === first && runs[runs.length - 1].at(-1) === last) {
    runs[0] = [...(runs.pop() ?? []), ...runs[0]];
  }
  const name = (v: number) => names[v - first] ?? String(v);
  return runs.map((r) => (r.length === 1 ? name(r[0]) : `${name(r[0])}-${name(r[r.length - 1])}`)).join(", ");
}

/** "Weekday peak (Nov-Mar, Mon-Fri, 16:00-21:00)". All months or all days are left out. */
export function windowLabel(w: DemandWindow): string {
  const parts = [formatRuns(w.months, SHORT_MONTHS, 1), formatRuns(w.days_of_week, SHORT_DAYS, 0)].filter((p) => p !== "");
  return `${w.label} (${[...parts, `${formatTime(w.start_time)}-${formatTime(w.end_time)}`].join(", ")})`;
}

export interface WindowChoices {
  windows: DemandWindow[];
  isPending: boolean;
  error: Error | null;
  refetch: () => void;
}

/** Demand windows of the tariff in force that have not ended. */
export function useCurrentDemandWindows(siteId: number): WindowChoices {
  const tariffs = useTariffs(siteId);
  const tariff = currentTariff(tariffs.data ?? []);
  const windows = useDemandWindows(siteId, tariff?.id ?? Number.NaN);
  const failed = tariffs.isError ? tariffs : windows.isError ? windows : null;
  return {
    windows: (windows.data ?? []).filter((w) => !hasEnded(w)),
    isPending: tariffs.isPending || (tariff !== undefined && windows.isPending),
    error: failed?.error ?? null,
    refetch: () => void failed?.refetch(),
  };
}
