export const inputClass =
  "rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60";
export const primaryButtonClass =
  "rounded-md bg-solar px-3 py-1.5 text-sm font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50";
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
