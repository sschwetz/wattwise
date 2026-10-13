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
                {window.effective_to === null && (
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

function AddWindowForm({ onAdd, isPending, error }: { onAdd: (input: RateWindowInput) => void; isPending: boolean; error: string | null }) {
  const [channel, setChannel] = useState<RateChannel>("buy");
  const [label, setLabel] = useState("");
  const [rate, setRate] = useState("");
  const [months, setMonths] = useState(ALL_MONTHS);
  const [days, setDays] = useState(ALL_DAYS);
  const [start, setStart] = useState("00:00");
  const [end, setEnd] = useState("00:00");
  const [from, setFrom] = useState(() => new Date().toISOString().slice(0, 10));

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    onAdd({
      channel,
      label: label.trim(),
      effective_from: new Date(`${from}T00:00:00`).toISOString(),
      effective_to: null,
      months,
      days_of_week: days,
      start_time: `${start}:00`,
      end_time: `${end}:00`,
      rate_c_per_kwh: Number(rate),
    });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2 border-t border-line pt-3 text-sm">
      {error && (
        <p role="alert" className={errorClass}>
          {error}
        </p>
      )}
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
          Label
          <input aria-label="Label" value={label} onChange={(e) => setLabel(e.target.value)} required autoComplete="off" className={inputClass} />
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
      <CheckboxGroup legend="Months" options={MONTH_NAMES.map((name, i) => ({ value: i + 1, label: name }))} selected={months} onChange={setMonths} />
      <CheckboxGroup legend="Days" options={DAY_NAMES.map((name, i) => ({ value: i, label: name }))} selected={days} onChange={setDays} />
      <button
        type="submit"
        disabled={isPending || months.length === 0 || days.length === 0}
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

  const actions: WindowActions = {
    onEnd: (window) =>
      replaceWindow.mutate({ windowId: window.id, input: toInput(window, { effective_to: new Date().toISOString() }) }),
    onDelete: (window) => deleteWindow.mutate(window.id),
    isPending: replaceWindow.isPending || deleteWindow.isPending,
  };
  const failure = [createWindow, replaceWindow, deleteWindow].find((m) => m.isError)?.error;

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
      <AddWindowForm
        onAdd={(input) => createWindow.mutate(input)}
        isPending={createWindow.isPending}
        error={createWindow.isError ? createWindow.error.message : null}
      />
      {failure && !createWindow.isError && (
        <p role="alert" className={errorClass}>
          {failure instanceof Error ? failure.message : "That change failed. Please try again."}
        </p>
      )}
    </div>
  );
}
