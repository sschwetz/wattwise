import { useId } from "react";
import { type EditorModel, formatUnit, invalidProps } from "./editorModel";
import { ParamField } from "./ParamField";
import { type ActionForm, findAction, type FormParam, moveAction, removeAction, resetActionParams } from "./scheduleForm";
import { FieldError } from "./Step";
import { hintClass, inputClass, labelClass } from "./styles";

const ZONE_TEMPERATURE = "set_zone_temperature";
const iconButtonClass =
  "rounded-md px-2 py-1 text-sm text-ink-muted underline hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:no-underline disabled:opacity-40";

/** The range the zone allows right now, from the unit's latest readings. Null when unknown. */
function allowedRange(model: EditorModel, card: ActionForm): string | null {
  const zone = card.params.zone;
  if (zone?.kind !== "value" || typeof zone.value !== "string") return null;
  const range = model.readings?.zone_ranges_c?.[zone.value];
  return range ? `Allowed now: ${range[0]}–${range[1]}${formatUnit("C")}` : null;
}

function ZoneRangeHint({ model, card }: { model: EditorModel; card: ActionForm }) {
  const range = allowedRange(model, card);
  return (
    <div className="space-y-1">
      {range && <p className={hintClass}>{range}</p>}
      <p className={hintClass}>Order matters: an earlier temperature change moves this range.</p>
    </div>
  );
}

function CardButtons({ model, card }: { model: EditorModel; card: ActionForm }) {
  const { form, setForm } = model;
  const index = form.actions.findIndex((c) => c.key === card.key);
  const n = index + 1;
  return (
    <div className="flex gap-1">
      <button type="button" aria-label={`Move action ${n} up`} disabled={index === 0} onClick={() => setForm(moveAction(form, card.key, -1))} className={iconButtonClass}>
        Up
      </button>
      <button type="button" aria-label={`Move action ${n} down`} disabled={index === form.actions.length - 1} onClick={() => setForm(moveAction(form, card.key, 1))} className={iconButtonClass}>
        Down
      </button>
      <button type="button" aria-label={`Remove action ${n}`} disabled={form.actions.length <= 1} onClick={() => setForm(removeAction(form, card.key))} className={iconButtonClass}>
        Remove
      </button>
    </div>
  );
}

function ActionPicker({ model, card }: { model: EditorModel; card: ActionForm }) {
  const errorId = useId();
  const { form, setForm, errors, capabilities } = model;
  const action = findAction(capabilities, card.actionId);
  const error = errors[`actions.${card.key}.action`];
  function pick(id: string) {
    const next = findAction(capabilities, id);
    if (next) setForm(resetActionParams(form, card.key, next, capabilities));
  }
  return (
    <div className="space-y-1">
      <label className={labelClass}>
        Action
        <select value={card.actionId} onChange={(e) => pick(e.target.value)} className={inputClass} {...invalidProps(errorId, error)}>
          {!action && <option value={card.actionId}>{card.actionId || "None"} (not offered by this device)</option>}
          {capabilities.actions.map((a) => (
            <option key={a.id} value={a.id}>{a.label}</option>
          ))}
        </select>
      </label>
      <FieldError id={errorId} message={error} />
    </div>
  );
}

/** One action in the list: the picker, the params it declares and the buttons that remove or move it. */
export function ActionCard({ model, card }: { model: EditorModel; card: ActionForm }) {
  const { form, setForm, errors, capabilities } = model;
  const action = findAction(capabilities, card.actionId);
  const number = form.actions.findIndex((c) => c.key === card.key) + 1;
  function setParam(name: string, value: FormParam) {
    setForm({ ...form, actions: form.actions.map((c) => (c.key === card.key ? { ...c, params: { ...c.params, [name]: value } } : c)) });
  }
  return (
    <div role="group" aria-label={`Action ${number}`} className="space-y-3 rounded-md border border-line bg-surface p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <ActionPicker model={model} card={card} />
        </div>
        <CardButtons model={model} card={card} />
      </div>
      {action?.params.map((param) => (
        <ParamField
          key={param.name}
          param={param}
          input={{ value: card.params[param.name], onChange: (value) => setParam(param.name, value), error: errors[`actions.${card.key}.param.${param.name}`] }}
        />
      ))}
      {card.actionId === ZONE_TEMPERATURE && <ZoneRangeHint model={model} card={card} />}
    </div>
  );
}
