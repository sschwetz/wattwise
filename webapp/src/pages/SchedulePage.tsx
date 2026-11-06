import { useState } from "react";
import { AddInstanceForm } from "../actors/AddInstanceForm";
import { ScheduleDetail } from "../actors/ScheduleDetail";
import { ScheduleList, type Selection } from "../actors/ScheduleList";
import { DisabledNotice, NeedsSignInNotice, SignInPanel } from "../actors/SignInPanel";
import { type ActorInstance, type ActorSchedule, isSigningIn } from "../actors/types";
import { useGuardedSelection } from "../actors/unsavedEdits";
import { useActorInstances, useSchedules } from "../actors/useActors";
import { QueryError } from "../components/QueryError";
import { Spinner } from "../components/Spinner";
import { AccessNote } from "../settings/AccessNote";
import { useSiteAccess } from "../settings/useSiteAccess";
import type { Site } from "../sites/useSites";

function needsSignIn(i: ActorInstance): boolean {
  return i.auth_state !== null && "needs_sign_in" in i.auth_state;
}

function isBlocked(i: ActorInstance): boolean {
  return isSigningIn(i.auth_state) || needsSignIn(i) || !i.enabled || i.capabilities === null;
}

/** The notice or panel for an instance that cannot run schedules yet, or null when it can. */
function BlockedInstance({ site, instance, canEdit }: { site: Site; instance: ActorInstance; canEdit: boolean }) {
  const common = { siteId: site.id, instance, canEdit };
  if (isSigningIn(instance.auth_state)) {
    return <SignInPanel auth={instance.auth_state} timezone={site.timezone} canEdit={canEdit} />;
  }
  if (needsSignIn(instance)) return <NeedsSignInNotice {...common} />;
  if (!instance.enabled) return <DisabledNotice {...common} />;
  if (instance.capabilities === null) {
    return <p className="text-sm text-ink-muted">Waiting for the actor to report what this device can do.</p>;
  }
  return null;
}

/** A picked schedule that no longer exists falls back to the first one. */
function effectiveSelection(picked: Selection, schedules: ActorSchedule[]): Selection {
  if (picked?.kind === "new") return picked;
  const first: Selection = schedules[0] ? { kind: "schedule", id: schedules[0].id } : null;
  if (picked?.kind === "schedule" && schedules.some((s) => s.id === picked.id)) return picked;
  return first;
}

function Workspace({ site, instances, schedules, canEdit }: {
  site: Site;
  instances: ActorInstance[];
  schedules: ActorSchedule[];
  canEdit: boolean;
}) {
  const [picked, setPicked] = useState<Selection>(null);
  const selected = effectiveSelection(picked, schedules);
  const { select, provide, dialog } = useGuardedSelection(selected, setPicked);
  const current = selected?.kind === "schedule" ? schedules.find((s) => s.id === selected.id) ?? null : null;
  const instance = instances.find((i) => i.id === current?.instance_id) ?? instances[0];
  const blocked = instances.map((i) => <BlockedInstance key={i.id} site={site} instance={i} canEdit={canEdit} />);
  if (schedules.length === 0 && instances.some(isBlocked)) return <div className="space-y-4">{blocked}</div>;
  return (
    <div className="space-y-4">
      {blocked}
      <div className="grid gap-4 md:grid-cols-[18rem_1fr]">
        <ScheduleList site={site} data={{ instances, schedules }} view={{ selected, onSelect: select, canEdit }} />
        {selected && provide(<ScheduleDetail siteId={site.id} schedule={current} instance={instance} canEdit={canEdit} onSelect={select} />)}
      </div>
      {dialog}
    </div>
  );
}

/** The Schedule tab of Site settings. */
export function ScheduleTab({ site }: { site: Site }) {
  const { readOnly, note } = useSiteAccess(site);
  const instances = useActorInstances(site.id);
  const schedules = useSchedules(site.id);
  const failed = instances.isError ? instances : schedules.isError ? schedules : null;
  let body;
  if (failed) body = <QueryError error={failed.error} fallback="Could not load the schedule." onRetry={() => void failed.refetch()} />;
  else if (!instances.data || !schedules.data) body = <Spinner />;
  else if (instances.data.length === 0) body = <AddInstanceForm siteId={site.id} canEdit={!readOnly} />;
  else body = <Workspace key={site.id} site={site} instances={instances.data} schedules={schedules.data} canEdit={!readOnly} />;
  return (
    <div className="space-y-4">
      <h1 className="sr-only">Schedule</h1>
      <AccessNote note={note} />
      {body}
    </div>
  );
}
