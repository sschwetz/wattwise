import { SubmitButton } from "../components/SubmitButton";
import { type FormEvent, useState } from "react";
import {
  byLabel,
  disabledClass,
  errorClass,
  formatCents,
  formatDaysOfWeek,
  formatMonths,
  formatTime,
  hasEnded,
  inputClass,
  primaryButtonClass,
} from "./billingUi";
import { CheckboxGroup, DAY_OPTIONS, MONTH_OPTIONS } from "./CheckboxGroup";
import { DateRangeFields, WindowDates } from "./DateFields";
import { useDateRange, useSiteTimeZone } from "./dateHooks";
import { useConditionalCreditWindows, useCreateConditionalCreditWindow, useEndConditionalCreditWindow } from "./useBilling";

interface TariffProps {
  siteId: number;
  tariffId: number;
}

export function ConditionalCreditList({ siteId, tariffId }: TariffProps) {
  const { data: windows } = useConditionalCreditWindows(siteId, tariffId);
  const endWindow = useEndConditionalCreditWindow(siteId, tariffId);
  const timeZone = useSiteTimeZone(siteId);

  return (
    <ul className="space-y-2">
      {byLabel(windows).map((w) => (
        <li key={w.id} className={`space-y-0.5 rounded-md border border-line/60 px-3 py-1.5 ${hasEnded(w) ? disabledClass : ""}`}>
          <div className="flex items-baseline justify-between gap-2 text-sm">
            <span className="text-ink">{w.label}</span>
            <span className="font-display tabular-nums text-ink">{formatCents(w.credit_c)}c credit</span>
          </div>
          <p className="text-xs text-ink-muted">
            Average import below {Number(w.max_avg_import_w)} W · {formatTime(w.start_time)}–{formatTime(w.end_time)}{" "}
            · {formatDaysOfWeek(w.days_of_week)} · {formatMonths(w.months)}
          </p>
          <WindowDates
            window={w}
            timeZone={timeZone}
            pending={endWindow.isPending}
            onEnd={(effective_to) => endWindow.mutate({ id: w.id, effective_to })}
          />
        </li>
      ))}
    </ul>
  );
}

export function ConditionalCreditForm({ siteId, tariffId }: TariffProps) {
  const [label, setLabel] = useState("");
  const [months, setMonths] = useState<number[]>([]);
  const [daysOfWeek, setDaysOfWeek] = useState<number[]>([]);
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [maxAvgImportW, setMaxAvgImportW] = useState("");
  const [creditC, setCreditC] = useState("");
  const createWindow = useCreateConditionalCreditWindow(siteId, tariffId);
  const { data: windows } = useConditionalCreditWindows(siteId, tariffId);
  const { range, toInput } = useDateRange(windows, siteId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createWindow.mutate({
      ...toInput(),
      label,
      months,
      days_of_week: daysOfWeek,
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
      <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Credit label"
          aria-label="Credit label"
          className={`${inputClass} col-span-full`}
          required
        />
        <DateRangeFields range={range} name="Credit" />
        <CheckboxGroup legend="Credit months" options={MONTH_OPTIONS} selection={{ selected: months, onChange: setMonths }} />
        <CheckboxGroup legend="Credit days of week" options={DAY_OPTIONS} selection={{ selected: daysOfWeek, onChange: setDaysOfWeek }} />
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
          className={`${inputClass} col-span-full`}
          required
        />
        <input
          value={creditC}
          onChange={(e) => setCreditC(e.target.value)}
          placeholder="Credit (cents)"
          aria-label="Credit (cents)"
          inputMode="decimal"
          className={`${inputClass} col-span-full`}
          required
        />
        <SubmitButton
          mutation={createWindow}
          disabled={months.length === 0 || daysOfWeek.length === 0}
          buttonClass={primaryButtonClass}
          layoutClass="col-span-full justify-self-start"
        >
          Add conditional credit
        </SubmitButton>
      </form>
    </div>
  );
}
