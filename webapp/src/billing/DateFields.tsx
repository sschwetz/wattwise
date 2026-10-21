import { type FormEvent, useState } from "react";
import { useSites } from "../sites/useSites";
import { endOfDayIso, localDate, startOfDayIso } from "./dates";
import { endLinkClass, inputClass, primaryButtonClass } from "./billingUi";

/** The site's time zone, or the browser's while the site list is loading. */
export function useSiteTimeZone(siteId: number): string {
  const { data } = useSites();
  const site = Array.isArray(data) ? data.find((s) => s.id === siteId) : undefined;
  return site?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
}

export function todayIn(timeZone: string): string {
  return localDate(new Date().toISOString(), timeZone);
}

interface DateInputProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
}

export function DateInput({ label, value, onChange, required }: DateInputProps) {
  return (
    <label className="flex flex-col text-xs text-ink-muted">
      {label}
      <input
        type="date"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={label}
        required={required}
        className={inputClass}
      />
    </label>
  );
}

export interface DateRangeValue {
  start: string;
  end: string;
  onStartChange: (value: string) => void;
  onEndChange: (value: string) => void;
}

/** The start date (required) and last day (optional) of a new item. `name`
 * prefixes the labels when a page has several such forms. */
export function DateRangeFields({ range, name = "" }: { range: DateRangeValue; name?: string }) {
  const prefix = name ? `${name} ` : "";
  const startLabel = name ? `${prefix}start date` : "Start date";
  return (
    <>
      <DateInput label={startLabel} value={range.start} onChange={range.onStartChange} required />
      <DateInput label={`${prefix}${name ? "end" : "End"} date (optional)`} value={range.end} onChange={range.onEndChange} />
    </>
  );
}

/** The state behind DateRangeFields. The start date follows `suggested` until
 * the user types one. `toInput` gives the API fields for the chosen dates. */
export function useDateRange(suggested: string, timeZone: string) {
  const [typedStart, setStart] = useState<string | null>(null);
  const [end, setEnd] = useState("");
  const start = typedStart ?? suggested;
  const range: DateRangeValue = { start, end, onStartChange: setStart, onEndChange: setEnd };
  const toInput = () => ({
    effective_from: startOfDayIso(start, timeZone),
    ...(end ? { effective_to: endOfDayIso(end, timeZone) } : {}),
  });
  return { range, toInput };
}

interface EndActionProps {
  label: string;
  timeZone: string;
  onConfirm: (effectiveTo: string) => void;
  pending?: boolean;
}

/** A link that opens an End date field; Confirm end sends the instant the
 * item stops applying, which is 00:00 local on the day after that date. */
export function EndAction({ label, timeZone, onConfirm, pending }: EndActionProps) {
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(() => todayIn(timeZone));

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    onConfirm(endOfDayIso(date, timeZone));
    setOpen(false);
  }

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className={endLinkClass}>
        {label}
      </button>
    );
  }
  return (
    <form onSubmit={handleSubmit} className="flex items-end gap-2">
      <DateInput label="End date" value={date} onChange={setDate} required />
      <button type="submit" disabled={pending} className={primaryButtonClass}>
        Confirm end
      </button>
      <button type="button" onClick={() => setOpen(false)} className={endLinkClass}>
        Cancel
      </button>
    </form>
  );
}
