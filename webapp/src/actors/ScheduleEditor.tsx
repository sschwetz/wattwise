import { type FormEvent, useState } from "react";
import { errorMessage } from "../api/client";
import type { DemandWindow } from "../billing/useBilling";
import { type SaveState, SubmitButton } from "../components/SubmitButton";
import { ReadOnlyFieldset } from "../components/ReadOnlyFieldset";
import { ActionStep } from "./ActionStep";
import { ActorStep } from "./ActorStep";
import { ConditionsEditor } from "./ConditionsEditor";
import { type DeleteControls, DeleteSchedule } from "./DeleteSchedule";
import type { EditorModel } from "./editorModel";
import { RestoreStep } from "./RestoreStep";
import { RetryStep } from "./RetryStep";
import { alignParams, applyRestorable, conditionReadings, emptyForm, fromSchedule, type SchedulePayload, toPayload, validateForm } from "./scheduleForm";
import { Step } from "./Step";
import { noticeClass, primaryButtonClass } from "./styles";
import type { ActorInstance, ActorSchedule, Capabilities } from "./types";
import { WhenStep } from "./WhenStep";

export interface EditorSetup {
  title: string;
  schedule: ActorSchedule | null;
  instance: ActorInstance;
  capabilities: Capabilities;
  windows: DemandWindow[];
  canEdit: boolean;
}

export interface EditorActions {
  save: (payload: SchedulePayload) => void;
  saveState: SaveState;
  saveError: Error | null;
  /** Null for a schedule that has not been saved yet. */
  remove: DeleteControls | null;
}

function initialForm({ schedule, instance, capabilities, windows }: EditorSetup) {
  const form = schedule ? fromSchedule(schedule) : emptyForm(capabilities, { instanceId: instance.id, firstWindowId: windows[0]?.id ?? null });
  return applyRestorable(alignParams(form, capabilities), capabilities);
}

function EnabledSwitch({ checked, onChange, disabled }: { checked: boolean; onChange: (on: boolean) => void; disabled: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label="Enabled"
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative h-6 w-11 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50 ${checked ? "bg-solar" : "bg-line"}`}
      >
        <span className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${checked ? "translate-x-5" : ""}`} />
      </button>
      <span className="text-sm text-ink">Enabled</span>
    </div>
  );
}

function OnlyIfStep({ model }: { model: EditorModel }) {
  const { form, setForm, errors, canEdit } = model;
  return (
    <Step number={3} title="Only if">
      <ConditionsEditor
        model={{
          legend: "Only if", legendHidden: true, rows: form.conditions, errors, canEdit, errorPrefix: "conditions",
          hint: "Every ticked condition must be true, or the run is skipped.",
          readings: conditionReadings(model.capabilities),
          onChange: (conditions) => setForm({ ...form, conditions }),
        }}
      />
    </Step>
  );
}

function Buttons({ actions }: { actions: EditorActions }) {
  return (
    <div className="space-y-3">
      {actions.saveError && <p role="alert" className={noticeClass}>{errorMessage(actions.saveError, "Could not save the schedule. Please try again.")}</p>}
      <SubmitButton mutation={actions.saveState} buttonClass={primaryButtonClass}>Save schedule</SubmitButton>
    </div>
  );
}

/** The numbered schedule form. Each step gets the same model: the form, its setter and the current errors. */
export function ScheduleEditor({ setup, actions }: { setup: EditorSetup; actions: EditorActions }) {
  const [form, setForm] = useState(() => initialForm(setup));
  const [submitted, setSubmitted] = useState(false);
  const { canEdit, capabilities } = setup;
  const errors = submitted ? validateForm(form, capabilities) : {};
  const model: EditorModel = { form, setForm, errors, capabilities, canEdit };

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitted(true);
    if (Object.keys(validateForm(form, capabilities)).length === 0) actions.save(toPayload(form, capabilities));
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-medium text-ink">{setup.title}</h2>
        <EnabledSwitch checked={form.enabled} onChange={(enabled) => setForm({ ...form, enabled })} disabled={!canEdit} />
      </div>
      <form onSubmit={handleSubmit} noValidate className="space-y-6">
        <ReadOnlyFieldset readOnly={!canEdit}>
          <ActorStep model={model} instance={setup.instance} />
          <WhenStep model={model} windows={setup.windows} />
          <OnlyIfStep model={model} />
          <ActionStep model={model} />
          <RestoreStep model={model} />
          <RetryStep model={model} />
        </ReadOnlyFieldset>
        {canEdit && <Buttons actions={actions} />}
      </form>
      {canEdit && actions.remove && <DeleteSchedule controls={actions.remove} />}
    </div>
  );
}
