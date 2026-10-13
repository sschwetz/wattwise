import { type FormEvent, useMemo, useState } from "react";
import { hasCoverageGap } from "./rateCoverage";
import {
  type RateChannel,
  type RateWindow,
  type RateWindowInput,
  useCreateRateWindow,
  useDeleteRateWindow,
  useRateWindows,
  useReplaceRateWindow,
} from "./useRateWindows";

const ALL_MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const CHANNEL_SECTIONS: { channel: RateChannel; title: string }[] = [
  { channel: "buy", title: "Import" },
  { channel: "sell", title: "Export (feed-in)" },
  { channel: "controlled_load", title: "Controlled load" },
];

const inputClass =
  "rounded-md border border-line bg-surface px-2 py-1 text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60";
const errorClass = "rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger";
const linkButtonClass = "text-ink-muted underline hover:text-ink disabled:opacity-50";

function describePattern(window: RateWindow): string {
  const months = window.months.length === 12 ? "all year" : window.months.map((m) => MONTH_NAMES[m - 1]).join(", ");
  const days = window.days_of_week.length === 7 ? "every day" : window.days_of_week.map((d) => DAY_NAMES[d]).join(", ");
  const allDay = window.start_time === window.end_time;
  const times = allDay ? "all day" : `${window.start_time.slice(0, 5)}–${window.end_time.slice(0, 5)}`;
  return `${times}, ${days}, ${months}`;
}

function formatRate(rate: string): string {
  return `${Number(rate)}c/kWh`;
}

function localDateString(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** A window starting today starts now: core-api rejects a start before the end
 * of a window the user has just ended. The collector prices only from the
 * current hour on, so a later start today changes no stored price. */
function effectiveFromFor(dateString: string): string {
  const now = new Date();
  if (dateString === localDateString(now)) return now.toISOString();
  return new Date(`${dateString}T00:00:00`).toISOString();
}

function toInput(window: RateWindow, overrides: Partial<RateWindowInput>): RateWindowInput {
  return {
    channel: window.channel,
    label: window.label,
    effective_from: window.effective_from,
    effective_to: window.effective_to,
    months: window.months,
    days_of_week: window.days_of_week,
    start_time: window.start_time,
    end_time: window.end_time,
    rate_c_per_kwh: Number(window.rate_c_per_kwh),
    ...overrides,
  };
}

interface WindowActions {
  onEnd: (window: RateWindow) => void;
  onDelete: (window: RateWindow) => void;
  isPending: boolean;
}

function ChannelSection({ title, windows, actions }: { title: string; windows: RateWindow[]; actions: WindowActions }) {
  const [now] = useState(() => Date.now());
  // A window that has not started yet is removed with Delete, not ended.
  const canEnd = (window: RateWindow) => window.effective_to === null && Date.parse(window.effective_from) <= now;
  return (
    <section className="space-y-1">
      <h4 className="text-sm font-semibold text-ink">{title}</h4>
      {windows.length === 0 ? (
        <p className="text-sm text-ink-muted">No windows.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {windows.map((window) => (
            <li key={window.id} className="flex justify-between gap-2 rounded-md border border-line px-2 py-1">
              <span className="text-ink">
                <span className="font-medium">{window.label}</span> — {formatRate(window.rate_c_per_kwh)} —{" "}
                {describePattern(window)}
                {window.effective_to && (
                  <span className="text-ink-muted"> (ended {new Date(window.effective_to).toLocaleDateString()})</span>
                )}
              </span>
              <span className="flex shrink-0 gap-3">
                {canEnd(window) && (
                  <button
                    aria-label={`End ${window.label}`}
                    onClick={() => actions.onEnd(window)}
                    disabled={actions.isPending}
                    className={linkButtonClass}
                  >
                    End
                  </button>
                )}
                <button
                  aria-label={`Delete ${window.label}`}
                  onClick={() => actions.onDelete(window)}
                  disabled={actions.isPending}
                  className={linkButtonClass}
                >
                  Delete
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CheckboxGroup({
  legend,
  options,
  selected,
  onChange,
}: {
  legend: string;
  options: { value: number; label: string }[];
  selected: number[];
  onChange: (selected: number[]) => void;
}) {
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

function AddWindowForm({ onAdd, isPending }: { onAdd: (input: RateWindowInput, onAdded: () => void) => void; isPending: boolean }) {
  const [channel, setChannel] = useState<RateChannel>("buy");
  const [label, setLabel] = useState("");
  const [rate, setRate] = useState("");
  const [months, setMonths] = useState(ALL_MONTHS);
  const [days, setDays] = useState(ALL_DAYS);
  const [start, setStart] = useState("00:00");
  const [end, setEnd] = useState("00:00");
  const [from, setFrom] = useState(() => localDateString(new Date()));

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!label.trim()) return;
    const clear = () => {
      setLabel("");
      setRate("");
    };
    onAdd({
      channel,
      label: label.trim(),
      effective_from: effectiveFromFor(from),
      effective_to: null,
      months,
      days_of_week: days,
      start_time: `${start}:00`,
      end_time: `${end}:00`,
      rate_c_per_kwh: Number(rate),
    }, clear);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2 border-t border-line pt-3 text-sm">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-ink-muted">
          Direction
          <select aria-label="Direction" value={channel} onChange={(e) => setChannel(e.target.value as RateChannel)} className={inputClass}>
            {CHANNEL_SECTIONS.map((s) => (
              <option key={s.channel} value={s.channel}>
                {s.title}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-ink-muted">
          Window label
          <input aria-label="Window label" value={label} onChange={(e) => setLabel(e.target.value)} required autoComplete="off" className={inputClass} />
        </label>
        <label className="flex flex-col text-ink-muted">
          Rate (c/kWh)
          <input aria-label="Rate (c/kWh)" type="number" step="0.0001" min="0" value={rate} onChange={(e) => setRate(e.target.value)} required className={inputClass} />
        </label>
        <label className="flex flex-col text-ink-muted">
          From
          <input aria-label="From" type="date" value={from} onChange={(e) => setFrom(e.target.value)} required className={inputClass} />
        </label>
        <label className="flex flex-col text-ink-muted">
          Start
          <input aria-label="Start" type="time" value={start} onChange={(e) => setStart(e.target.value)} required className={inputClass} />
        </label>
        <label className="flex flex-col text-ink-muted">
          End
          <input aria-label="End" type="time" value={end} onChange={(e) => setEnd(e.target.value)} required className={inputClass} />
        </label>
      </div>
      <p className="text-xs text-ink-muted">Start and end at the same time cover the whole day. An end before the start runs past midnight.</p>
      <p className="text-xs text-ink-muted">To change a rate, End the old window, then add the new one starting today or later.</p>
      <CheckboxGroup legend="Months" options={MONTH_NAMES.map((name, i) => ({ value: i + 1, label: name }))} selected={months} onChange={setMonths} />
      <CheckboxGroup legend="Days" options={DAY_NAMES.map((name, i) => ({ value: i, label: name }))} selected={days} onChange={setDays} />
      <button
        type="submit"
        disabled={isPending || !label.trim() || months.length === 0 || days.length === 0}
        className="rounded-md bg-solar px-3 py-1 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
      >
        Add window
      </button>
    </form>
  );
}

export function RateWindowsPanel({ siteId, sourceId }: { siteId: number; sourceId: number }) {
  const { data: windows, isError: loadFailed } = useRateWindows(siteId, sourceId);
  const createWindow = useCreateRateWindow(siteId, sourceId);
  const replaceWindow = useReplaceRateWindow(siteId, sourceId);
  const deleteWindow = useDeleteRateWindow(siteId, sourceId);
  const gap = useMemo(() => hasCoverageGap(windows ?? []), [windows]);

  // Each call clears every earlier result, so at most one error is ever shown
  // and it belongs to the latest change.
  function resetAll() {
    createWindow.reset();
    replaceWindow.reset();
    deleteWindow.reset();
  }
  const actions: WindowActions = {
    onEnd: (window) => {
      resetAll();
      replaceWindow.mutate({ windowId: window.id, input: toInput(window, { effective_to: new Date().toISOString() }) });
    },
    onDelete: (window) => {
      resetAll();
      deleteWindow.mutate(window.id);
    },
    isPending: replaceWindow.isPending || deleteWindow.isPending,
  };
  const handleAdd = (input: RateWindowInput, onAdded: () => void) => {
    resetAll();
    createWindow.mutate(input, { onSuccess: onAdded });
  };
  const error = createWindow.error ?? replaceWindow.error ?? deleteWindow.error;

  return (
    <div className="space-y-3 border-t border-line p-3">
      <h3 className="font-display text-base font-semibold text-ink">Rate windows</h3>
      {gap && (
        <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-sm text-ink">
          Some months, days or times are not covered by a window. Those minutes will have no price.
        </p>
      )}
      {loadFailed && (
        <p role="alert" className={errorClass}>
          Could not load rate windows.
        </p>
      )}
      {/* Sections wait for the data so a loading list never reads "No windows." */}
      {windows &&
        CHANNEL_SECTIONS.map(({ channel, title }) => (
          <ChannelSection key={channel} title={title} windows={windows.filter((w) => w.channel === channel)} actions={actions} />
        ))}
      {error && (
        <p role="alert" className={errorClass}>
          {error.message}
        </p>
      )}
      <AddWindowForm onAdd={handleAdd} isPending={createWindow.isPending} />
    </div>
  );
}
