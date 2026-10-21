export const inputClass =
  "rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60";
export const primaryButtonClass =
  "rounded-md bg-solar px-3 py-1.5 text-sm font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50";
export const endLinkClass = "text-sm text-ink-muted underline hover:text-ink";
// Applied to anything shown but no longer active (e.g. an ended window or an
// expired fee discount) -- kept visible rather than removed, but struck
// through and muted.
export const disabledClass = "text-ink-muted line-through";
export const errorClass = "rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger";

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function formatMonths(months: number[]): string {
  if (new Set(months).size === 12) return "all year";
  return months.map((m) => MONTH_NAMES[m - 1] ?? String(m)).join(", ");
}

export function formatDaysOfWeek(days: number[]): string {
  if (new Set(days).size === 7) return "every day";
  return days.map((d) => DAY_NAMES[d] ?? String(d)).join(", ");
}

export function formatCents(value: string): string {
  return Number(value).toFixed(2);
}

export function formatTime(time: string): string {
  return time.slice(0, 5);
}

export function parseIntList(raw: string): number[] {
  return raw
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part.length > 0)
    .map(Number);
}

export function byLabel<T extends { label: string }>(windows: T[] | undefined): T[] {
  return [...(windows ?? [])].sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
}

/** Has this item's end date passed? An item with no end date has not. */
export function hasEnded(item: { effective_to: string | null }): boolean {
  return item.effective_to !== null && Date.parse(item.effective_to) <= Date.now();
}
