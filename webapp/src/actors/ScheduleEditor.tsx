import { type FormEvent, useId, useState } from "react";
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
import { alignParams, applyRestorable, conditionReadings, emptyForm, fromSchedule, type SchedulePayload, toPayload } from "./scheduleForm";
import { isDirty } from "./scheduleDirty";
import { validateForm } from "./scheduleValidation";
import { Step } from "./Step";
import { noticeClass, primaryButtonClass } from "./styles";
import type { ActorInstance, ActorSchedule, Capabilities } from "./types";
import { useReportDirty } from "./unsavedEdits";
import { WhenStep } from "./WhenStep";

export interface EditorSetup {
  title: string;
  schedule: ActorSchedule | null;
  instance: ActorInstance;
  capabilities: Capabilities;
  windows: DemandWindow[];
  /** The site's time zone. */
  timezone: string;
  canEdit: boolean;
}

export interface EditorActions {
  /** `onSaved` runs once the server has accepted the payload. */
  save: (payload: SchedulePayload, onSaved: () => void) => void;
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
  const labelId = useId();
  return (
    <div className="flex items-center gap-3">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelId}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative h-6 w-11 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50 ${checked ? "bg-solar" : "bg-line"}`}
      >
        <span className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${checked ? "translate-x-5" : ""}`} />
      </button>
      <span id={labelId} className="text-sm text-ink">Enabled</span>
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
          calendar: { windows: model.windows, timezone: model.timezone },
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

/** The form state, its errors and the submit handler. The baseline is the form as last
 * saved, or as first shown; the editor is dirty while the form differs from it. A viewer
 * cannot change anything, so is never dirty. */
function useEditorForm(setup: EditorSetup, actions: EditorActions) {
  const [start] = useState(() => initialForm(setup));
  const [form, setForm] = useState(start);
  const [baseline, setBaseline] = useState(start);
  const [submitted, setSubmitted] = useState(false);
  const { canEdit, capabilities } = setup;
  const reportDirty = useReportDirty(canEdit && isDirty(form, baseline));
  const errors = submitted ? validateForm(form, capabilities, setup.windows) : {};
  const model: EditorModel = { form, setForm, errors, capabilities, canEdit, windows: setup.windows, timezone: setup.timezone, readings: setup.instance.latest_readings };

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitted(true);
    if (Object.keys(validateForm(form, capabilities, setup.windows)).length > 0) return;
    actions.save(toPayload(form, capabilities), () => {
      setBaseline(form);
      reportDirty(false);
    });
  }
  return { model, handleSubmit };
}

/** The numbered schedule form. Each step gets the same model: the form, its setter and the current errors. */
export function ScheduleEditor({ setup, actions }: { setup: EditorSetup; actions: EditorActions }) {
  const { model, handleSubmit } = useEditorForm(setup, actions);
  const { form, setForm, canEdit } = model;
  // While a new schedule is saving, edits would be lost when the editor moves to it.
  const locked = !canEdit || (setup.schedule === null && actions.saveState.isPending);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-medium text-ink">{setup.title}</h2>
        <EnabledSwitch checked={form.enabled} onChange={(enabled) => setForm({ ...form, enabled })} disabled={locked} />
      </div>
      <form onSubmit={handleSubmit} noValidate className="space-y-6">
        <ReadOnlyFieldset readOnly={locked}>
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
