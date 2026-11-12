import { useId } from "react";
import { type EditorModel, invalidProps } from "./editorModel";
import { ParamField } from "./ParamField";
import { findAction, type FormParam, resetActionParams } from "./scheduleForm";
import { FieldError, Step } from "./Step";
import { hintClass, inputClass, labelClass } from "./styles";

/** Step 4: the actions and params come from what the actor declared.
 * Shows the first action only until the list editor replaces this. */
export function ActionStep({ model }: { model: EditorModel }) {
  const errorId = useId();
  const { form, setForm, errors, capabilities } = model;
  const card = form.actions[0];
  const action = findAction(capabilities, card.actionId);
  const errorKey = `actions.${card.key}`;
  function pick(id: string) {
    const next = findAction(capabilities, id);
    if (next) setForm(resetActionParams(form, card.key, next));
  }
  function setParam(name: string, value: FormParam) {
    setForm({ ...form, actions: form.actions.map((c) => (c.key === card.key ? { ...c, params: { ...c.params, [name]: value } } : c)) });
  }
  return (
    <Step number={4} title="Do this">
      <label className={labelClass}>
        Action
        <select value={card.actionId} onChange={(e) => pick(e.target.value)} className={inputClass} {...invalidProps(errorId, errors[`${errorKey}.action`])}>
          {!action && <option value={card.actionId}>{card.actionId || "None"} (not offered by this device)</option>}
          {capabilities.actions.map((a) => (
            <option key={a.id} value={a.id}>{a.label}</option>
          ))}
        </select>
      </label>
      <FieldError id={errorId} message={errors[`${errorKey}.action`]} />
      {action?.params.map((param) => (
        <ParamField
          key={param.name}
          param={param}
          input={{
            value: card.params[param.name],
            onChange: (value) => setParam(param.name, value),
            error: errors[`${errorKey}.param.${param.name}`],
          }}
        />
      ))}
      <p className={hintClass}>This list and the fields below it come from the device.</p>
    </Step>
  );
}
