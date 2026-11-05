import { useId } from "react";
import { type EditorModel, invalidProps } from "./editorModel";
import { ParamField } from "./ParamField";
import { findAction, resetActionParams } from "./scheduleForm";
import { FieldError, Step } from "./Step";
import { hintClass, inputClass, labelClass } from "./styles";

/** Step 4: the actions and params come from what the actor declared. */
export function ActionStep({ model }: { model: EditorModel }) {
  const errorId = useId();
  const { form, setForm, errors, capabilities } = model;
  const action = findAction(capabilities, form.actionId);
  function pick(id: string) {
    const next = findAction(capabilities, id);
    if (next) setForm(resetActionParams(form, next));
  }
  return (
    <Step number={4} title="Do this">
      <label className={labelClass}>
        Action
        <select value={form.actionId} onChange={(e) => pick(e.target.value)} className={inputClass} {...invalidProps(errorId, errors.action)}>
          {!action && <option value={form.actionId}>{form.actionId || "None"} (not offered by this device)</option>}
          {capabilities.actions.map((a) => (
            <option key={a.id} value={a.id}>{a.label}</option>
          ))}
        </select>
      </label>
      <FieldError id={errorId} message={errors.action} />
      {action?.params.map((param) => (
        <ParamField
          key={param.name}
          param={param}
          input={{
            value: form.params[param.name],
            onChange: (value) => setForm({ ...form, params: { ...form.params, [param.name]: value } }),
            error: errors[`param.${param.name}`],
          }}
        />
      ))}
      <p className={hintClass}>This list and the fields below it come from the device.</p>
    </Step>
  );
}
