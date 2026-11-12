import { ActionCard } from "./ActionCard";
import type { EditorModel } from "./editorModel";
import { addAction, MAX_ACTIONS } from "./scheduleForm";
import { FieldError, Step } from "./Step";
import { hintClass, secondaryButtonClass } from "./styles";

/** Step 4: an ordered list of actions. The choices and params come from what the actor declared. */
export function ActionStep({ model }: { model: EditorModel }) {
  const { form, setForm, errors, capabilities } = model;
  return (
    <Step number={4} title="Do this">
      {form.actions.map((card) => (
        <ActionCard key={card.key} model={model} card={card} />
      ))}
      <FieldError id="actions-error" message={errors.actions} />
      {form.actions.length < MAX_ACTIONS && (
        <button type="button" onClick={() => setForm(addAction(form, capabilities))} className={secondaryButtonClass}>
          Add action
        </button>
      )}
      <p className={hintClass}>This list and the fields below it come from the device. Actions run in the order shown.</p>
    </Step>
  );
}
