import { type FormEvent, useState } from "react";
import { CHANNEL_SECTIONS, SHORT_DAYS, SHORT_MONTHS, inputClass } from "./rateWindowUi";
import type { RateChannel, RateWindowInput } from "./useRateWindows";

interface FormValues {
  channel: RateChannel;
  label: string;
  rate: string;
  from: string;
  start: string;
  end: string;
  months: number[];
  days: number[];
}

type Setter = <K extends keyof FormValues>(key: K) => (value: FormValues[K]) => void;

const MONTH_OPTIONS = SHORT_MONTHS.map((name, i) => ({ value: i + 1, label: name }));
const DAY_OPTIONS = SHORT_DAYS.map((name, i) => ({ value: i, label: name }));

function localDateString(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** A window starting today starts now: core-api rejects a start before the end
 * of a window the user has just ended. A window added with today's date
 * starts at the current instant; the collector rounds its start up to the
 * next whole minute. */
function effectiveFromFor(dateString: string): string {
  const now = new Date();
  if (dateString === localDateString(now)) return now.toISOString();
  return new Date(`${dateString}T00:00:00`).toISOString();
}

function initialValues(): FormValues {
  return {
    channel: "buy",
    label: "",
    rate: "",
    from: localDateString(new Date()),
    start: "00:00",
    end: "00:00",
    months: MONTH_OPTIONS.map((o) => o.value),
    days: DAY_OPTIONS.map((o) => o.value),
  };
}

function toWindowInput(values: FormValues): RateWindowInput {
  return {
    channel: values.channel,
    label: values.label.trim(),
    effective_from: effectiveFromFor(values.from),
    effective_to: null,
    months: values.months,
    days_of_week: values.days,
    start_time: `${values.start}:00`,
    end_time: `${values.end}:00`,
    rate_c_per_kwh: Number(values.rate),
  };
}

function useWindowForm() {
  const [values, setValues] = useState(initialValues);
  const set: Setter = (key) => (value) => setValues((v) => ({ ...v, [key]: value }));
  const clear = () => setValues((v) => ({ ...v, label: "", rate: "" }));
  return { values, set, clear };
}

interface FieldSpec {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "number" | "date" | "time";
}

function Field({ field }: { field: FieldSpec }) {
  const { label, value, onChange, type = "text" } = field;
  const numberProps = type === "number" ? { step: "0.0001", min: "0" } : {};
  return (
    <label className="flex flex-col text-ink-muted">
      {label}
      <input
        aria-label={label}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required
        autoComplete="off"
        className={inputClass}
        {...numberProps}
      />
    </label>
  );
}

function DirectionSelect({ value, onChange }: { value: RateChannel; onChange: (channel: RateChannel) => void }) {
  return (
    <label className="flex flex-col text-ink-muted">
      Direction
      <select aria-label="Direction" value={value} onChange={(e) => onChange(e.target.value as RateChannel)} className={inputClass}>
        {CHANNEL_SECTIONS.map((s) => (
          <option key={s.channel} value={s.channel}>
            {s.title}
          </option>
        ))}
      </select>
    </label>
  );
}

function WindowFields({ values, set }: { values: FormValues; set: Setter }) {
  return (
    <div className="flex flex-wrap items-end gap-2">
      <DirectionSelect value={values.channel} onChange={set("channel")} />
      <Field field={{ label: "Window label", value: values.label, onChange: set("label") }} />
      <Field field={{ label: "Rate (c/kWh)", value: values.rate, onChange: set("rate"), type: "number" }} />
      <Field field={{ label: "From", value: values.from, onChange: set("from"), type: "date" }} />
      <Field field={{ label: "Start", value: values.start, onChange: set("start"), type: "time" }} />
      <Field field={{ label: "End", value: values.end, onChange: set("end"), type: "time" }} />
    </div>
  );
}

interface Selection {
  selected: number[];
  onChange: (selected: number[]) => void;
}

function CheckboxGroup({ legend, options, selection }: { legend: string; options: { value: number; label: string }[]; selection: Selection }) {
  const { selected, onChange } = selection;
  function toggle(value: number) {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value].sort((a, b) => a - b));
  }
  return (
    <fieldset className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-ink-muted">
      <legend>{legend}</legend>
      {options.map((option) => (
        <label key={option.value} className="flex items-center gap-1">
          <input type="checkbox" checked={selected.includes(option.value)} onChange={() => toggle(option.value)} />
          {option.label}
        </label>
      ))}
    </fieldset>
  );
}

export function AddWindowForm({ onAdd, isPending }: { onAdd: (input: RateWindowInput, onAdded: () => void) => void; isPending: boolean }) {
  const { values, set, clear } = useWindowForm();
  const canSubmit = !isPending && values.label.trim() !== "" && values.months.length > 0 && values.days.length > 0;

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (canSubmit) onAdd(toWindowInput(values), clear);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2 border-t border-line pt-3 text-sm">
      <WindowFields values={values} set={set} />
      <p className="text-xs text-ink-muted">Start and end at the same time cover the whole day. An end before the start runs past midnight.</p>
      <p className="text-xs text-ink-muted">To change a rate, End the old window, then add the new one starting today or later.</p>
      <CheckboxGroup legend="Months" options={MONTH_OPTIONS} selection={{ selected: values.months, onChange: set("months") }} />
      <CheckboxGroup legend="Days" options={DAY_OPTIONS} selection={{ selected: values.days, onChange: set("days") }} />
      <button
        type="submit"
        disabled={!canSubmit}
        className="rounded-md bg-solar px-3 py-1 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
      >
        Add window
      </button>
    </form>
  );
}
