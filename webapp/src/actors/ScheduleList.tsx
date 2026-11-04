import type { ActorInstance, ActorSchedule } from "./types";
import { buttonClass, cardClass, primaryButtonClass } from "./styles";

export type Selection = { kind: "schedule"; id: number } | { kind: "new" } | null;

export interface ScheduleListProps {
  instances: ActorInstance[];
  schedules: ActorSchedule[];
  selected: Selection;
  onSelect: (selection: Selection) => void;
  canEdit: boolean;
}

function OnOffPill({ on }: { on: boolean }) {
  const tone = on ? "bg-flow-soft text-flow" : "bg-surface-2 text-ink-muted";
  return <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${tone}`}>{on ? "On" : "Off"}</span>;
}

function ScheduleRow({ schedule, instance, active, onClick }: {
  schedule: ActorSchedule;
  instance: ActorInstance;
  active: boolean;
  onClick: () => void;
}) {
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
        </span>
        <OnOffPill on={schedule.enabled} />
      </button>
    </li>
  );
}

function InstanceGroup({ instance, rows, props }: { instance: ActorInstance; rows: ActorSchedule[]; props: ScheduleListProps }) {
  const { instances, selected, onSelect } = props;
  return (
    <div className="space-y-1">
      {instances.length > 1 && <h3 className="px-3 text-xs font-medium uppercase text-ink-muted">{instance.name}</h3>}
      <ul className="space-y-1">
        {rows.map((s) => (
          <ScheduleRow
            key={s.id}
            schedule={s}
            instance={instance}
            active={selected?.kind === "schedule" && selected.id === s.id}
            onClick={() => onSelect({ kind: "schedule", id: s.id })}
          />
        ))}
      </ul>
    </div>
  );
}

export function ScheduleList(props: ScheduleListProps) {
  const { instances, schedules, selected, onSelect, canEdit } = props;
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
          instance={instance}
          rows={schedules.filter((s) => s.instance_id === instance.id)}
          props={props}
        />
      ))}
    </section>
  );
}
