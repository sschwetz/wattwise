import { type FormEvent, useState } from "react";
import {
  errorClass,
  formatCents,
  formatDaysOfWeek,
  formatMonths,
  formatTime,
  inputClass,
  parseIntList,
  primaryButtonClass,
} from "./billingUi";
import { useConditionalCreditWindows, useCreateConditionalCreditWindow } from "./useBilling";

interface TariffProps {
  siteId: number;
  tariffId: number;
}

export function ConditionalCreditList({ siteId, tariffId }: TariffProps) {
  const { data: windows } = useConditionalCreditWindows(siteId, tariffId);

  return (
    <ul className="space-y-2">
      {(windows ?? []).map((w) => (
        <li key={w.id} className="space-y-0.5 rounded-md border border-line/60 px-3 py-1.5">
          <div className="flex items-baseline justify-between gap-2 text-sm">
            <span className="text-ink">{w.label}</span>
            <span className="font-display tabular-nums text-ink">{formatCents(w.credit_c)}c credit</span>
          </div>
          <p className="text-xs text-ink-muted">
            Average import below {Number(w.max_avg_import_w)} W · {formatTime(w.start_time)}–{formatTime(w.end_time)}{" "}
            · {formatDaysOfWeek(w.days_of_week)} · {formatMonths(w.months)}
          </p>
        </li>
      ))}
    </ul>
  );
}

export function ConditionalCreditForm({ siteId, tariffId }: TariffProps) {
  const [label, setLabel] = useState("");
  const [months, setMonths] = useState("");
  const [daysOfWeek, setDaysOfWeek] = useState("");
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [maxAvgImportW, setMaxAvgImportW] = useState("");
  const [creditC, setCreditC] = useState("");
  const createWindow = useCreateConditionalCreditWindow(siteId, tariffId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createWindow.mutate({
      label,
      months: parseIntList(months),
      days_of_week: parseIntList(daysOfWeek),
      start_time: startTime,
      end_time: endTime,
      max_avg_import_w: Number(maxAvgImportW),
      credit_c: Number(creditC),
    });
  }

  return (
    <div className="ml-2 space-y-2 border-l-2 border-line pl-4">
      {createWindow.isError && (
        <p role="alert" className={errorClass}>
          {createWindow.error instanceof Error
            ? createWindow.error.message
            : "Failed to create conditional credit. Please try again."}
        </p>
      )}
      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Credit label"
          aria-label="Credit label"
          className={inputClass}
          required
        />
        <input
          value={months}
          onChange={(e) => setMonths(e.target.value)}
          placeholder="Credit months (e.g. 12,1,2)"
          aria-label="Credit months"
          inputMode="numeric"
          className={inputClass}
          required
        />
        <input
          value={daysOfWeek}
          onChange={(e) => setDaysOfWeek(e.target.value)}
          placeholder="Credit days of week (0=Mon .. 6=Sun)"
          aria-label="Credit days of week"
          inputMode="numeric"
          className={inputClass}
          required
        />
        <input
          value={startTime}
          onChange={(e) => setStartTime(e.target.value)}
          placeholder="Credit start time (HH:MM)"
          aria-label="Credit start time"
          className={inputClass}
          required
        />
        <input
          value={endTime}
          onChange={(e) => setEndTime(e.target.value)}
          placeholder="Credit end time (HH:MM)"
          aria-label="Credit end time"
          className={inputClass}
          required
        />
        <input
          value={maxAvgImportW}
          onChange={(e) => setMaxAvgImportW(e.target.value)}
          placeholder="Average import below (W)"
          aria-label="Average import below (W)"
          inputMode="decimal"
          className={inputClass}
          required
        />
        <input
          value={creditC}
          onChange={(e) => setCreditC(e.target.value)}
          placeholder="Credit (cents)"
          aria-label="Credit (cents)"
          inputMode="decimal"
          className={inputClass}
          required
        />
        <button type="submit" disabled={createWindow.isPending} className={primaryButtonClass}>
          Add conditional credit
        </button>
      </form>
    </div>
  );
}
