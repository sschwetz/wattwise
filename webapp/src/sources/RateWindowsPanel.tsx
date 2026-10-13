import { useMemo, useState } from "react";
import { AddWindowForm } from "./AddWindowForm";
import { type CoverageGap, findCoverageGaps } from "./rateCoverage";
import { CHANNEL_SECTIONS, SHORT_DAYS, SHORT_MONTHS, channelTitle, errorClass } from "./rateWindowUi";
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
  onEnd: (window: RateWindow) => void;
  onDelete: (window: RateWindow) => void;
  isPending: boolean;
}

/** The three window changes. Each one clears every earlier result first, so at
 * most one error is ever shown and it belongs to the latest change. */
function useWindowChanges(siteId: number, sourceId: number) {
  const createWindow = useCreateRateWindow(siteId, sourceId);
  const replaceWindow = useReplaceRateWindow(siteId, sourceId);
  const deleteWindow = useDeleteRateWindow(siteId, sourceId);
  const all = [createWindow, replaceWindow, deleteWindow];
  const resetAll = () => all.forEach((m) => m.reset());

  const actions: WindowActions = {
    onEnd: (window) => {
      resetAll();
      replaceWindow.mutate({ windowId: window.id, input: toInput(window, { effective_to: new Date().toISOString() }) });
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

function WindowRow({ window, canEnd, actions }: { window: RateWindow; canEnd: boolean; actions: WindowActions }) {
  return (
    <li className="flex justify-between gap-2 rounded-md border border-line px-2 py-1">
      <span className="text-ink">
        <span className="font-medium">{window.label}</span> — {formatRate(window.rate_c_per_kwh)} — {describePattern(window)}
        {window.effective_to && (
          <span className="text-ink-muted"> (ended {new Date(window.effective_to).toLocaleDateString()})</span>
        )}
      </span>
      <span className="flex shrink-0 gap-3">
        {canEnd && (
          <button aria-label={`End ${window.label}`} onClick={() => actions.onEnd(window)} disabled={actions.isPending} className={linkButtonClass}>
            End
          </button>
        )}
        <button aria-label={`Delete ${window.label}`} onClick={() => actions.onDelete(window)} disabled={actions.isPending} className={linkButtonClass}>
          Delete
        </button>
      </span>
    </li>
  );
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
            <WindowRow key={window.id} window={window} canEnd={canEnd(window)} actions={actions} />
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
  const { actions, onAdd, error } = useWindowChanges(siteId, sourceId);
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
      <AddWindowForm onAdd={onAdd} isPending={actions.isPending} />
    </div>
  );
}
