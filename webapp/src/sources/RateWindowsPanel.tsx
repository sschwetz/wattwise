import { useMemo, useState } from "react";
import { EndAction } from "../billing/DateFields";
import { useSiteTimeZone } from "../billing/dateHooks";
import { displayEndDate, formatDay } from "../billing/dates";
import { AddWindowForm } from "./AddWindowForm";
import { type CoverageGap, findCoverageGaps } from "./rateCoverage";
import { CHANNEL_SECTIONS, SHORT_DAYS, SHORT_MONTHS, channelTitle, errorClass, inputClass } from "./rateWindowUi";
import {
  type RateWindow,
  type RateWindowInput,
  useCreateRateWindow,
  useDeleteRateWindow,
  useRateWindows,
  useReplaceRateWindow,
} from "./useRateWindows";

const linkButtonClass = "text-ink-muted underline hover:text-ink disabled:opacity-50";

function describePattern(window: RateWindow): string {
  const months = window.months.length === 12 ? "all year" : window.months.map((m) => SHORT_MONTHS[m - 1]).join(", ");
  const days = window.days_of_week.length === 7 ? "every day" : window.days_of_week.map((d) => SHORT_DAYS[d]).join(", ");
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
  /** Ends the window at `effectiveTo`, the start of the day after its last day. */
  onEnd: (window: RateWindow, effectiveTo: string) => void;
  timeZone: string;
  /** Sends the window back with only its rate changed; `onSaved` runs on success. */
  onEditRate: (window: RateWindow, rate: number, onSaved: () => void) => void;
  onDelete: (window: RateWindow) => void;
  isPending: boolean;
}

/** The three window changes. Each one clears every earlier result first, so at
 * most one error is ever shown and it belongs to the latest change. */
function useWindowChanges(siteId: number, sourceId: number, timeZone: string) {
  const createWindow = useCreateRateWindow(siteId, sourceId);
  const replaceWindow = useReplaceRateWindow(siteId, sourceId);
  const deleteWindow = useDeleteRateWindow(siteId, sourceId);
  const all = [createWindow, replaceWindow, deleteWindow];
  const resetAll = () => all.forEach((m) => m.reset());

  const actions: WindowActions = {
    onEnd: (window, effectiveTo) => {
      resetAll();
      replaceWindow.mutate({ windowId: window.id, input: toInput(window, { effective_to: effectiveTo }) });
    },
    timeZone,
    onEditRate: (window, rate, onSaved) => {
      resetAll();
      replaceWindow.mutate({ windowId: window.id, input: toInput(window, { rate_c_per_kwh: rate }) }, { onSuccess: onSaved });
    },
    onDelete: (window) => {
      resetAll();
      deleteWindow.mutate(window.id);
    },
    isPending: all.some((m) => m.isPending),
  };
  const onAdd = (input: RateWindowInput, onAdded: () => void) => {
    resetAll();
    createWindow.mutate(input, { onSuccess: onAdded });
  };
  const error = all.map((m) => m.error).find((e) => e !== null) ?? null;
  return { actions, onAdd, error };
}

function WindowSummary({ window, timeZone }: { window: RateWindow; timeZone: string }) {
  return (
    <span className="text-ink">
      <span className="font-medium">{window.label}</span> — {formatRate(window.rate_c_per_kwh)} — {describePattern(window)}
      {window.effective_to && (
        <span className="text-ink-muted">
          {" "}
          (ended {formatDay(displayEndDate(window.effective_to, timeZone))})
        </span>
      )}
    </span>
  );
}

function RowButtons({ window, actions, onEdit }: { window: RateWindow; actions: WindowActions; onEdit: () => void }) {
  const [now] = useState(() => Date.now());
  // A window that has not started yet is removed with Delete, not ended.
  const canEnd = window.effective_to === null && Date.parse(window.effective_from) <= now;
  const button = (text: string, ariaPrefix: string, onClick: () => void) => (
    <button aria-label={`${ariaPrefix} ${window.label}`} onClick={onClick} disabled={actions.isPending} className={linkButtonClass}>
      {text}
    </button>
  );
  return (
    <span className="flex shrink-0 gap-3">
      {button("Edit rate", "Edit rate of", onEdit)}
      {canEnd && (
        <EndAction
          label="End"
          ariaLabel={`End ${window.label}`}
          timeZone={actions.timeZone}
          pending={actions.isPending}
          onConfirm={(effectiveTo) => actions.onEnd(window, effectiveTo)}
        />
      )}
      {button("Delete", "Delete", () => actions.onDelete(window))}
    </span>
  );
}

/** Changes only the rate, so core-api reprices from the window's own start. */
function RateEditor({ window, actions, onClose }: { window: RateWindow; actions: WindowActions; onClose: () => void }) {
  const current = Number(window.rate_c_per_kwh);
  const [value, setValue] = useState(() => String(current));
  const rate = value.trim() === "" ? Number.NaN : Number(value);
  const canSave = Number.isFinite(rate) && rate >= 0 && rate !== current && !actions.isPending;
  return (
    <div className="flex items-end gap-3">
      <label className="flex flex-col text-ink-muted">
        New rate (c/kWh)
        <input aria-label="New rate (c/kWh)" type="number" step="0.0001" min="0" value={value} onChange={(e) => setValue(e.target.value)} className={inputClass} />
      </label>
      <button onClick={() => actions.onEditRate(window, rate, onClose)} disabled={!canSave} className={linkButtonClass}>
        Save
      </button>
      <button onClick={onClose} className={linkButtonClass}>
        Cancel
      </button>
    </div>
  );
}

function WindowRow({ window, actions }: { window: RateWindow; actions: WindowActions }) {
  const [editing, setEditing] = useState(false);
  return (
    <li className="space-y-1 rounded-md border border-line px-2 py-1">
      <div className="flex justify-between gap-2">
        <WindowSummary window={window} timeZone={actions.timeZone} />
        <RowButtons window={window} actions={actions} onEdit={() => setEditing(true)} />
      </div>
      {editing && <RateEditor window={window} actions={actions} onClose={() => setEditing(false)} />}
    </li>
  );
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
            <WindowRow key={window.id} window={window} actions={actions} />
          ))}
        </ul>
      )}
    </section>
  );
}

function CoverageWarning({ gaps }: { gaps: CoverageGap[] }) {
  if (gaps.length === 0) return null;
  const parts = gaps.map((gap) => `${channelTitle(gap.channel)} (for example ${gap.example})`);
  return (
    <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-sm text-ink">
      Not covered: {parts.join("; ")}. Those minutes will have no price.
    </p>
  );
}

export function RateWindowsPanel({ siteId, sourceId }: { siteId: number; sourceId: number }) {
  const { data: windows, isError: loadFailed } = useRateWindows(siteId, sourceId);
  const timeZone = useSiteTimeZone(siteId);
  const { actions, onAdd, error } = useWindowChanges(siteId, sourceId, timeZone);
  const gaps = useMemo(() => findCoverageGaps(windows ?? []), [windows]);

  return (
    <div className="space-y-3 border-t border-line p-3">
      <h3 className="font-display text-base font-semibold text-ink">Rate windows</h3>
      <CoverageWarning gaps={gaps} />
      {loadFailed && <p role="alert" className={errorClass}>Could not load rate windows.</p>}
      {/* Sections wait for the data so a loading list never reads "No windows." */}
      {windows &&
        CHANNEL_SECTIONS.map(({ channel, title }) => (
          <ChannelSection key={channel} title={title} windows={windows.filter((w) => w.channel === channel)} actions={actions} />
        ))}
      {error && <p role="alert" className={errorClass}>{error.message}</p>}
      <AddWindowForm onAdd={onAdd} isPending={actions.isPending} windows={windows ?? []} timeZone={timeZone} />
    </div>
  );
}
