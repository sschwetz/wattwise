import { useId } from "react";
import { type EditorModel, invalidProps } from "./editorModel";
import { FieldError, Step } from "./Step";
import { hintClass, inputClass, labelClass } from "./styles";
import type { ActorInstance } from "./types";

/** Step 1: the schedule's name and the device it commands. */
export function ActorStep({ model, instance }: { model: EditorModel; instance: ActorInstance }) {
  const errorId = useId();
  const { form, setForm, errors } = model;
  return (
    <Step number={1} title="Actor">
      <label className={labelClass}>
        Name
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={200} className={inputClass} {...invalidProps(errorId, errors.name)} />
      </label>
      <FieldError id={errorId} message={errors.name} />
      <p className={hintClass}>
        Device: <span className="text-ink">{instance.name ?? "Air conditioner"}</span>
      </p>
    </Step>
  );
}
