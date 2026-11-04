import { useId } from "react";
import { ConditionsEditor } from "./ConditionsEditor";
import { type EditorModel, invalidProps } from "./editorModel";
import { conditionReadings, findAction, type RestoreForm, type RestoreWhen, restoreOptions } from "./scheduleForm";
import { FieldError, Step } from "./Step";
import { hintClass, inputClass, labelClass } from "./styles";

const WHEN_LABELS: Record<RestoreWhen, string> = {
  window_end: "When the window ends",
  after_minutes: "After a set number of minutes",
  at_time: "At a set time",
  conditions_only: "Only when a condition below is met",
};

function restoreSetter(model: EditorModel) {
  const { form, setForm } = model;
  return (patch: Partial<RestoreForm>) => setForm({ ...form, restore: { ...form.restore, ...patch } });
}

function WhenFields({ model }: { model: EditorModel }) {
  const errorId = useId();
  const change = restoreSetter(model);
  const { restore, trigger } = model.form;
  const error = model.errors[restore.when === "at_time" ? "restore.time" : "restore.minutes"];
  return (
    <div className="space-y-3">
      <label className={labelClass}>
        When to put it back
        <select value={restore.when} onChange={(e) => change({ when: e.target.value as RestoreWhen })} className={inputClass}>
          {restoreOptions(trigger.type).map((w) => (
            <option key={w} value={w}>{WHEN_LABELS[w]}</option>
          ))}
        </select>
      </label>
      {restore.when === "after_minutes" && (
        <label className={labelClass}>
          Minutes after it runs
          <input type="number" min={1} step={1} value={restore.minutes} onChange={(e) => change({ minutes: e.target.value })} className={`${inputClass} max-w-32`} {...invalidProps(errorId, error)} />
        </label>
      )}
      {restore.when === "at_time" && (
        <label className={labelClass}>
          Put it back at (HH:MM, site time)
          <input value={restore.time} onChange={(e) => change({ time: e.target.value })} placeholder="21:00" inputMode="numeric" className={`${inputClass} max-w-32`} {...invalidProps(errorId, error)} />
        </label>
      )}
      <FieldError id={errorId} message={restore.when === "after_minutes" || restore.when === "at_time" ? error : undefined} />
    </div>
  );
}

function RestoreConditions({ model }: { model: EditorModel }) {
  const change = restoreSetter(model);
  const { restore } = model.form;
  const common = { readings: conditionReadings(model.capabilities), errors: model.errors, canEdit: model.canEdit };
  return (
    <>
      <ConditionsEditor
        model={{ ...common, legend: "Only restore if", hint: "Every ticked condition must be true, or the device is left as it is.", rows: restore.onlyIf, onChange: (onlyIf) => change({ onlyIf }), errorPrefix: "restore.onlyIf" }}
      />
      <ConditionsEditor
        model={{ ...common, legend: "Restore early when", hint: "Puts it back as soon as every ticked condition is true.", rows: restore.earlyIf, onChange: (earlyIf) => change({ earlyIf }), errorPrefix: "restore.earlyIf" }}
      />
      <FieldError id="restore-early-error" message={model.errors["restore.earlyIf"]} />
    </>
  );
}

export function RestoreStep({ model }: { model: EditorModel }) {
  const change = restoreSetter(model);
  const restorable = findAction(model.capabilities, model.form.actionId)?.restorable ?? false;
  const enabled = restorable && model.form.restore.enabled;
  return (
    <Step number={5} title="When it ends">
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" className="accent-solar" checked={enabled} disabled={!restorable} onChange={(e) => change({ enabled: e.target.checked })} />
        Put it back how it was before
      </label>
      {!restorable && <p className={hintClass}>This action cannot be undone, so there is nothing to put back.</p>}
      {enabled && <WhenFields model={model} />}
      {enabled && <RestoreConditions model={model} />}
    </Step>
  );
}
