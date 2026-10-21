import { type FormEvent, useState } from "react";
import { displayEndDate, endOfDayIso, formatDay, localDate } from "./dates";
import { type DateRangeValue, todayIn } from "./dateHooks";
import { endLinkClass, inputClass, primaryButtonClass } from "./billingUi";

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

interface EndActionProps {
  label: string;
  /** Names the button for assistive technology when `label` alone is not enough. */
  ariaLabel?: string;
  timeZone: string;
  onConfirm: (effectiveTo: string) => void;
  pending?: boolean;
}

/** A link that opens an End date field; Confirm end sends the instant the
 * item stops applying, which is 00:00 local on the day after that date. */
export function EndAction({ label, ariaLabel, timeZone, onConfirm, pending }: EndActionProps) {
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(() => todayIn(timeZone));

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    onConfirm(endOfDayIso(date, timeZone));
    setOpen(false);
  }

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} disabled={pending} aria-label={ariaLabel} className={endLinkClass}>
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

interface WindowDatesProps {
  window: { label: string; effective_from: string; effective_to: string | null };
  timeZone: string;
  onEnd: (effectiveTo: string) => void;
  pending?: boolean;
}

/** A window's dates, with an End link while it has no end date. */
export function WindowDates({ window, timeZone, onEnd, pending }: WindowDatesProps) {
  const from = formatDay(localDate(window.effective_from, timeZone));
  return (
    <div className="flex items-center justify-between gap-2 text-xs text-ink-muted">
      <span>
        from {from}
        {window.effective_to && ` until ${formatDay(displayEndDate(window.effective_to, timeZone))}`}
      </span>
      {!window.effective_to && (
        <EndAction
          label="End"
          ariaLabel={`End ${window.label}`}
          timeZone={timeZone}
          onConfirm={onEnd}
          pending={pending}
        />
      )}
    </div>
  );
}
