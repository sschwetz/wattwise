import type { SaveState } from "../components/SubmitButton";
import { QueryError } from "../components/QueryError";
import { Spinner } from "../components/Spinner";
import { useSites } from "../sites/useSites";
import { useCurrentDemandWindows } from "./demandWindows";
import { type EditorActions, ScheduleEditor } from "./ScheduleEditor";
import type { Selection } from "./ScheduleList";
import type { SchedulePayload } from "./scheduleForm";
import { ScheduleRunHistory } from "./RunHistory";
import { cardClass } from "./styles";
import { TestNow } from "./TestNow";
import { UnitStatePanel } from "./UnitStatePanel";
import type { ActorInstance, ActorSchedule } from "./types";
import { useCreateSchedule, useDeleteSchedule, useUpdateSchedule } from "./useActors";

export interface ScheduleDetailProps {
  siteId: number;
  /** Null while a new schedule is being started. */
  schedule: ActorSchedule | null;
  instance: ActorInstance;
  canEdit: boolean;
  /** Called with the new schedule after a create, and with null after a delete. */
  onSelect?: (selection: Selection) => void;
}

const IDLE: SaveState = { isPending: false, isSuccess: false, submittedAt: 0 };

/** Create, update and delete for the shown schedule. Mutation state from another
 * schedule is ignored, so a Saved note or an error does not follow the owner to the next one. */
function useScheduleActions({ siteId, schedule, onSelect }: ScheduleDetailProps): EditorActions {
  const create = useCreateSchedule(siteId);
  const update = useUpdateSchedule(siteId);
  const remove = useDeleteSchedule(siteId);
  const id = schedule?.id;
  // A create stays relevant after onSelect moves to the schedule it made.
  const relevant = [schedule === null || create.data?.id === id ? create : null, update.variables?.id === id ? update : null]
    .filter((m) => m !== null);
  const last = [...relevant].sort((a, b) => b.submittedAt - a.submittedAt)[0];
  return {
    save: (payload: SchedulePayload) => {
      if (id === undefined) create.mutate(payload, { onSuccess: (made) => onSelect?.({ kind: "schedule", id: made.id }) });
      else {
        const { instance_id: _instance, ...patch } = payload;
        update.mutate({ id, ...patch });
      }
    },
    saveState: last ?? IDLE,
    saveError: last?.error ?? null,
    remove: id === undefined ? null : {
      onConfirm: () => remove.mutate(id, { onSuccess: () => onSelect?.(null) }),
      isPending: remove.isPending,
      error: remove.variables === id ? remove.error : null,
    },
  };
}

function DetailBody(props: ScheduleDetailProps & { title: string }) {
  const { siteId, schedule, instance, canEdit, title } = props;
  const windows = useCurrentDemandWindows(siteId);
  const actions = useScheduleActions(props);
  const heading = <h2 className="text-lg font-medium text-ink">{title}</h2>;
  if (instance.capabilities === null) {
    return <>{heading}<p className="text-sm text-ink-muted">Waiting for the actor to report what this device can do.</p></>;
  }
  if (windows.error) return <>{heading}<QueryError error={windows.error} fallback="Could not load the demand windows." onRetry={windows.refetch} /></>;
  if (windows.isPending) return <>{heading}<Spinner /></>;
  return (
    <ScheduleEditor
      key={`${instance.id}-${schedule?.id ?? "new"}`}
      setup={{ title, schedule, instance, capabilities: instance.capabilities, windows: windows.windows, canEdit }}
      actions={actions}
    />
  );
}

function Placeholder() {
  return <section className={cardClass}><Spinner /><span className="sr-only">Loading</span></section>;
}

export function ScheduleDetail(props: ScheduleDetailProps) {
  const { siteId, schedule, instance, canEdit } = props;
  const sites = useSites();
  const site = sites.data?.find((s) => s.id === siteId);
  return (
    <div className="space-y-4">
      <section className={`${cardClass} space-y-6`}>
        <DetailBody {...props} title={schedule ? schedule.name : "New schedule"} />
        {schedule && canEdit && <TestNow key={schedule.id} siteId={siteId} scheduleId={schedule.id} />}
      </section>
      {site ? <UnitStatePanel instance={instance} timezone={site.timezone} /> : <Placeholder />}
      {schedule && (site ? <ScheduleRunHistory site={site} schedule={schedule} instance={instance} /> : <Placeholder />)}
    </div>
  );
}
