import type { RateChannel } from "./useRateWindows";

export const CHANNEL_SECTIONS: { channel: RateChannel; title: string }[] = [
  { channel: "buy", title: "Import" },
  { channel: "sell", title: "Export (feed-in)" },
  { channel: "controlled_load", title: "Controlled load" },
];

export function channelTitle(channel: RateChannel): string {
  return CHANNEL_SECTIONS.find((s) => s.channel === channel)?.title ?? channel;
}

export const SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const SHORT_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const inputClass =
  "rounded-md border border-line bg-surface px-2 py-1 text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60";
export const errorClass = "rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger";
