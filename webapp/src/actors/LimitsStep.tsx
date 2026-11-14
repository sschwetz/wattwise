import { useId } from "react";
import { type EditorModel, invalidProps } from "./editorModel";
import { defaultClockRef, type LimitsForm, MAX_GAP_MINUTES } from "./scheduleForm";
import { FieldError, Step } from "./Step";
import { hintClass, inputClass, labelClass } from "./styles";
import { TimeRefField } from "./TimeRefField";

function limitsSetter(model: EditorModel) {
  const { form, setForm } = model;
  return (patch: Partial<LimitsForm>) => setForm({ ...form, limits: { ...form.limits, ...patch } });
}

function GapField({ model }: { model: EditorModel }) {
  const errorId = useId();
  const change = limitsSetter(model);
  const error = model.errors["limits.minGap"];
  return (
    <div className="space-y-1">
      <label className={labelClass}>
        Minutes between runs
        <input type="number" min={0} max={MAX_GAP_MINUTES} step={1} value={model.form.limits.minGap} onChange={(e) => change({ minGap: e.target.value })} className={`${inputClass} max-w-32`} {...invalidProps(errorId, error)} />
      </label>
      <p className={hintClass}>It will not run again until this long after it was last put back, or after it last ran if it is not put back. 0 turns this off. Air conditioners should not start and stop every few minutes.</p>
      <FieldError id={errorId} message={error} />
    </div>
  );
}

function AgainAfterField({ model }: { model: EditorModel }) {
  const errorId = useId();
  const change = limitsSetter(model);
  const { againAfter } = model.form.limits;
  const error = model.errors["limits.againAfter"];
  return (
    <div className="space-y-1">
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" className="accent-solar" checked={againAfter !== null} onChange={(e) => change({ againAfter: e.target.checked ? defaultClockRef("06:00") : null })} />
        Do not run again until
      </label>
      {againAfter !== null && (
        <div className="space-y-1 pl-6">
          <TimeRefField label="Run again after" value={againAfter} onChange={(next) => change({ againAfter: next })} error={{ id: errorId, message: error }} />
          <p className={hintClass}>Counted from the start of its last run. Skipped runs do not count.</p>
        </div>
      )}
      <FieldError id={errorId} message={error} />
    </div>
  );
}

/** Limits on how often the schedule fires. */
export function LimitsStep({ model }: { model: EditorModel }) {
  return (
    <Step number={6} title="How often">
      <GapField model={model} />
      <AgainAfterField model={model} />
    </Step>
  );
}
