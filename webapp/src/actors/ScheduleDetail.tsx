import { useContext } from "react";
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
import { UnsavedEditsContext } from "./unsavedEdits";
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

function withoutInstance({ instance_id: _instance, ...patch }: SchedulePayload) {
  return patch;
}

/** The time zone the browser is set to, used until the site loads. */
function browserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

const IDLE: SaveState = { isPending: false, isSuccess: false, submittedAt: 0 };

/** What follows a create or a delete. The editor is marked clean first, so the selection
 * change that follows does not ask about unsaved edits. */
function useAfterMutation(onSelect: ScheduleDetailProps["onSelect"]) {
  const reportDirty = useContext(UnsavedEditsContext);
  return {
    created: (onSaved: () => void) => (made: ActorSchedule) => {
      onSaved();
      onSelect?.({ kind: "schedule", id: made.id });
    },
    deleted: () => {
      reportDirty(false);
      onSelect?.(null);
    },
  };
}

/** Create, update and delete for the shown schedule. Mutation state from another
 * schedule is ignored, so a Saved note or an error does not follow the owner to the next one. */
function useScheduleActions({ siteId, schedule, onSelect }: ScheduleDetailProps): EditorActions {
  const create = useCreateSchedule(siteId);
  const update = useUpdateSchedule(siteId);
  const remove = useDeleteSchedule(siteId);
  const after = useAfterMutation(onSelect);
  const id = schedule?.id;
  // A create stays relevant after onSelect moves to the schedule it made.
  const relevant = [schedule === null || create.data?.id === id ? create : null, update.variables?.id === id ? update : null]
    .filter((m) => m !== null);
  const last = [...relevant].sort((a, b) => b.submittedAt - a.submittedAt)[0];
  return {
    save: (payload: SchedulePayload, onSaved: () => void) => {
      if (id === undefined) create.mutate(payload, { onSuccess: after.created(onSaved) });
      else update.mutate({ ...withoutInstance(payload), id }, { onSuccess: onSaved });
    },
    saveState: last ?? IDLE,
    saveError: last?.error ?? null,
    remove: id === undefined ? null : {
      onConfirm: () => remove.mutate(id, { onSuccess: after.deleted }),
      isPending: remove.isPending,
      error: remove.variables === id ? remove.error : null,
    },
  };
}

function DetailBody(props: ScheduleDetailProps & { title: string; timezone: string }) {
  const { siteId, schedule, instance, canEdit, title, timezone } = props;
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
      setup={{ title, schedule, instance, capabilities: instance.capabilities, windows: windows.windows, timezone, canEdit }}
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
        <DetailBody {...props} title={schedule ? schedule.name : "New schedule"} timezone={site?.timezone ?? browserTimezone()} />
        {schedule && canEdit && <TestNow key={schedule.id} siteId={siteId} scheduleId={schedule.id} />}
        {schedule && (site ? <ScheduleRunHistory site={site} schedule={schedule} instance={instance} /> : <Spinner />)}
      </section>
      {site ? <UnitStatePanel instance={instance} timezone={site.timezone} /> : <Placeholder />}
    </div>
  );
}
