import { formatRunTime, outcomeLabel } from "./runFormat";
import { useNow } from "../dashboard/useNow";
import type { ActorInstance, ActorRunListItem, ActorSchedule } from "./types";
import { useRuns } from "./useActors";
import { buttonClass, cardClass, primaryButtonClass } from "./styles";

export type Selection = { kind: "schedule"; id: number } | { kind: "new" } | null;

const RUN_WINDOW = 50;

export interface ScheduleListProps {
  site: { id: number; timezone: string };
  data: { instances: ActorInstance[]; schedules: ActorSchedule[] };
  view: { selected: Selection; onSelect: (selection: Selection) => void; canEdit: boolean };
}

function OnOffPill({ on }: { on: boolean }) {
  const tone = on ? "bg-flow-soft text-flow" : "bg-surface-2 text-ink-muted";
  return <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${tone}`}>{on ? "On" : "Off"}</span>;
}

/** "Last run Today 16:00 · OK" from the newest run in the site-wide list, or "No runs yet" once that list has loaded. */
function lastRunText(run: ActorRunListItem | undefined, timezone: string, now: Date): string {
  return run ? `Last run ${formatRunTime(run.trigger_at, timezone, now)} · ${outcomeLabel(run.outcome)}` : "No runs yet";
}

function ScheduleRow({ row, active, onClick }: {
  row: { schedule: ActorSchedule; instance: ActorInstance; lastRun: string | null };
  active: boolean;
  onClick: () => void;
}) {
  const { schedule, instance, lastRun } = row;
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        aria-current={active ? "true" : undefined}
        className={`flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 ${active ? "bg-surface-2" : ""}`}
      >
        <span className="min-w-0">
          <span className="block truncate text-ink">{schedule.name}</span>
          <span className="block truncate text-xs text-ink-muted">{instance.name ?? "Air conditioner"}</span>
          {lastRun && <span className="block truncate text-xs text-ink-muted">{lastRun}</span>}
        </span>
        <OnOffPill on={schedule.enabled} />
      </button>
    </li>
  );
}

function InstanceGroup({ group, view, lastRunOf }: {
  group: { instance: ActorInstance; rows: ActorSchedule[]; showName: boolean };
  view: ScheduleListProps["view"];
  lastRunOf: (scheduleId: number) => string | null;
}) {
  const { instance, rows, showName } = group;
  const { selected, onSelect } = view;
  return (
    <div className="space-y-1">
      {showName && <h3 className="px-3 text-xs font-medium uppercase text-ink-muted">{instance.name}</h3>}
      <ul className="space-y-1">
        {rows.map((s) => (
          <ScheduleRow
            key={s.id}
            row={{ schedule: s, instance, lastRun: lastRunOf(s.id) }}
            active={selected?.kind === "schedule" && selected.id === s.id}
            onClick={() => onSelect({ kind: "schedule", id: s.id })}
          />
        ))}
      </ul>
    </div>
  );
}

export function ScheduleList(props: ScheduleListProps) {
  const { site, data, view } = props;
  const { instances, schedules } = data;
  const { selected, onSelect, canEdit } = view;
  const runs = useRuns(site.id, RUN_WINDOW);
  const now = useNow(60_000);
  const lastRunOf = (id: number) => (runs.data ? lastRunText(runs.data.find((r) => r.schedule_id === id), site.timezone, now) : null);
  const ready = instances.some((i) => i.capabilities !== null);
  return (
    <section className={`${cardClass} space-y-3`}>
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-medium text-ink">Schedules</h2>
        {canEdit && (
          <button
            type="button"
            disabled={!ready}
            onClick={() => onSelect({ kind: "new" })}
            className={selected?.kind === "new" ? `${buttonClass} border border-line text-ink` : primaryButtonClass}
          >
            New schedule
          </button>
        )}
      </div>
      {schedules.length === 0 && <p className="text-sm text-ink-muted">No schedules yet.</p>}
      {instances.map((instance) => (
        <InstanceGroup
          key={instance.id}
          group={{ instance, rows: schedules.filter((s) => s.instance_id === instance.id), showName: instances.length > 1 }}
          view={view}
          lastRunOf={lastRunOf}
        />
      ))}
    </section>
  );
}
