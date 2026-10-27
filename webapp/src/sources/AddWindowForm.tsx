import { type FormEvent, useState } from "react";
import { Spinner } from "../components/Spinner";
import { CHANNEL_SECTIONS, inputClass } from "./rateWindowUi";
import { CheckboxGroup } from "../billing/CheckboxGroup";
import { DAY_OPTIONS, MONTH_OPTIONS } from "../billing/checkboxOptions";
import { todayIn } from "../billing/dateHooks";
import { endOfDayIso, startOfDayIso, suggestStart } from "../billing/dates";
import type { RateChannel, RateWindow, RateWindowInput } from "./useRateWindows";

interface FormValues {
  channel: RateChannel;
  label: string;
  rate: string;
  from: string | null;
  to: string;
  start: string;
  end: string;
  months: number[];
  days: number[];
}

type Setter = <K extends keyof FormValues>(key: K) => (value: FormValues[K]) => void;


function initialValues(): FormValues {
  return {
    channel: "buy",
    label: "",
    rate: "",
    from: null,
    to: "",
    start: "00:00",
    end: "00:00",
    months: MONTH_OPTIONS.map((o) => o.value),
    days: DAY_OPTIONS.map((o) => o.value),
  };
}

/** The dates are calendar days in the site's time zone. `from` is the day the
 * form shows, which is the suggested one until the user types another. */
function toWindowInput(values: FormValues, from: string, timeZone: string): RateWindowInput {
  return {
    channel: values.channel,
    label: values.label.trim(),
    effective_from: startOfDayIso(from, timeZone),
    effective_to: values.to ? endOfDayIso(values.to, timeZone) : null,
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
  required?: boolean;
}

function Field({ field }: { field: FieldSpec }) {
  const { label, value, onChange, type = "text", required = true } = field;
  const numberProps = type === "number" ? { step: "0.0001", min: "0" } : {};
  return (
    <label className="flex flex-col text-ink-muted">
      {label}
      <input
        aria-label={label}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={required}
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

function WindowFields({ values, from, set }: { values: FormValues; from: string; set: Setter }) {
  return (
    <div className="flex flex-wrap items-end gap-2">
      <DirectionSelect value={values.channel} onChange={set("channel")} />
      <Field field={{ label: "Window label", value: values.label, onChange: set("label") }} />
      <Field field={{ label: "Rate (c/kWh)", value: values.rate, onChange: set("rate"), type: "number" }} />
      <Field field={{ label: "Start date", value: from, onChange: set("from"), type: "date" }} />
      <Field field={{ label: "End date (optional)", value: values.to, onChange: set("to"), type: "date", required: false }} />
      <Field field={{ label: "Start", value: values.start, onChange: set("start"), type: "time" }} />
      <Field field={{ label: "End", value: values.end, onChange: set("end"), type: "time" }} />
    </div>
  );
}

interface AddWindowFormProps {
  onAdd: (input: RateWindowInput, onAdded: () => void) => void;
  isPending: boolean;
  /** The source's windows, to suggest a start date after the latest ended one. */
  windows: RateWindow[];
  timeZone: string;
}

export function AddWindowForm({ onAdd, isPending, windows, timeZone }: AddWindowFormProps) {
  const { values, set, clear } = useWindowForm();
  const sameDirection = windows.filter((w) => w.channel === values.channel);
  const from = values.from ?? suggestStart(sameDirection, timeZone, todayIn(timeZone));
  const canSubmit = !isPending && values.label.trim() !== "" && values.months.length > 0 && values.days.length > 0;

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (canSubmit) onAdd(toWindowInput(values, from, timeZone), clear);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2 border-t border-line pt-3 text-sm">
      <WindowFields values={values} from={from} set={set} />
      <p className="text-xs text-ink-muted">Start and end at the same time cover the whole day. An end before the start runs past midnight.</p>
      <p className="text-xs text-ink-muted">To change a rate, End the old window on its last day, then add the new one starting the next day.</p>
      <CheckboxGroup legend="Months" options={MONTH_OPTIONS} selection={{ selected: values.months, onChange: set("months") }} />
      <CheckboxGroup legend="Days" options={DAY_OPTIONS} selection={{ selected: values.days, onChange: set("days") }} />
      <button
        type="submit"
        disabled={!canSubmit}
        className="rounded-md bg-solar px-3 py-1 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50 inline-flex items-center gap-2"
      >
        {isPending && <Spinner className="h-3.5 w-3.5" />}
        Add window
      </button>
    </form>
  );
}
