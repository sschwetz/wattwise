import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { actionLabeler, formatRunTime, outcomeLabel } from "./runFormat";
import { useNow } from "../dashboard/useNow";
import { rowId, type ListRow } from "./scheduleLayout";
import { SeparatorRow } from "./SeparatorRow";
import { SortableItem } from "./SortableItem";
import type { ActorInstance, ActorRunListItem, ActorSchedule } from "./types";
import { useArrangement } from "./useArrangement";
import { useRuns } from "./useActors";
import { buttonClass, cardClass, noticeClass, primaryButtonClass } from "./styles";

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

/** The first action's name, with "+N more" when there are others. */
function actionsText(schedule: ActorSchedule, instance: ActorInstance): string {
  const [first, ...rest] = schedule.actions;
  if (!first) return "";
  const name = actionLabeler(instance)(first.id);
  return rest.length > 0 ? `${name} +${rest.length} more` : name;
}

function ScheduleRow({ row, active, onClick }: {
  row: { schedule: ActorSchedule; instance: ActorInstance; lastRun: string | null };
  active: boolean;
  onClick: () => void;
}) {
  const { schedule, instance, lastRun } = row;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      className={`flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 ${active ? "bg-surface-2" : ""}`}
    >
      <span className="min-w-0">
        <span className="block truncate text-ink">{schedule.name}</span>
        <span className="block truncate text-xs text-ink-muted">{instance.name ?? "Device"}</span>
        <span className="block truncate text-xs text-ink-muted">{actionsText(schedule, instance)}</span>
        {lastRun && <span className="block truncate text-xs text-ink-muted">{lastRun}</span>}
      </span>
      <OnOffPill on={schedule.enabled} />
    </button>
  );
}

function Header({ view, ready, onAddSeparator }: {
  view: ScheduleListProps["view"];
  ready: boolean;
  onAddSeparator: () => void;
}) {
  const { selected, onSelect, canEdit } = view;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-lg font-medium text-ink">Schedules</h2>
      {canEdit && (
        <div className="flex items-center gap-2">
          <button type="button" onClick={onAddSeparator} className={`${buttonClass} whitespace-nowrap border border-line text-ink`}>
            Add separator
          </button>
          <button
            type="button"
            disabled={!ready}
            onClick={() => onSelect({ kind: "new" })}
            className={`whitespace-nowrap ${selected?.kind === "new" ? `${buttonClass} border border-line text-ink` : primaryButtonClass}`}
          >
            New schedule
          </button>
        </div>
      )}
    </div>
  );
}

export function ScheduleList(props: ScheduleListProps) {
  const { site, data, view } = props;
  const { instances, schedules } = data;
  const runs = useRuns(site.id, RUN_WINDOW);
  const now = useNow(60_000);
  const arrangement = useArrangement(site.id, schedules);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const lastRunOf = (id: number) => (runs.data ? lastRunText(runs.data.find((r) => r.schedule_id === id), site.timezone, now) : null);
  const ready = instances.some((i) => i.capabilities !== null);
  return (
    <section className={`${cardClass} space-y-3`}>
      <Header view={view} ready={ready} onAddSeparator={arrangement.addSeparator} />
      {schedules.length === 0 && <p className="text-sm text-ink-muted">No schedules yet.</p>}
      {arrangement.saveFailed && <p role="alert" className={noticeClass}>Could not save the order.</p>}
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={arrangement.drop}>
        <SortableContext items={arrangement.rows.map(rowId)} strategy={verticalListSortingStrategy}>
          <ul className="space-y-1">
            {arrangement.rows.map((row) => (
              <ListItem key={rowId(row)} row={row} ctx={{ instances, view, arrangement, lastRunOf }} />
            ))}
          </ul>
        </SortableContext>
      </DndContext>
    </section>
  );
}

function ListItem({ row, ctx }: {
  row: ListRow;
  ctx: {
    instances: ActorInstance[];
    view: ScheduleListProps["view"];
    arrangement: ReturnType<typeof useArrangement>;
    lastRunOf: (scheduleId: number) => string | null;
  };
}) {
  const { instances, view, arrangement, lastRunOf } = ctx;
  const { selected, onSelect, canEdit } = view;
  if (row.kind === "separator") {
    const edit = canEdit
      ? { onRename: (label: string) => arrangement.renameSeparator(row.key, label), onRemove: () => arrangement.removeSeparator(row.key) }
      : null;
    return <SortableItem id={rowId(row)} canEdit={canEdit}><SeparatorRow row={row} edit={edit} /></SortableItem>;
  }
  const instance = instances.find((i) => i.id === row.schedule.instance_id);
  if (!instance) return null;
  const { id } = row.schedule;
  return (
    <SortableItem id={rowId(row)} canEdit={canEdit}>
      <ScheduleRow
        row={{ schedule: row.schedule, instance, lastRun: lastRunOf(id) }}
        active={selected?.kind === "schedule" && selected.id === id}
        onClick={() => onSelect({ kind: "schedule", id })}
      />
    </SortableItem>
  );
}
