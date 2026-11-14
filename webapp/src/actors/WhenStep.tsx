import { useId } from "react";
import { CheckboxGroup } from "../billing/CheckboxGroup";
import { DAY_OPTIONS } from "../billing/checkboxOptions";
import type { DemandWindow } from "../billing/useBilling";
import { windowLabel } from "./demandWindows";
import { type EditorModel, invalidProps } from "./editorModel";
import { isWindowTrigger, MAX_OFFSET_MINUTES, setTriggerType, suggestRearm, type TriggerForm, type TriggerType } from "./scheduleForm";
import { FieldError, Step } from "./Step";
import { hintClass, inputClass, labelClass } from "./styles";

const TRIGGERS: { type: TriggerType; label: string }[] = [
  { type: "window_start", label: "A demand window starts" },
  { type: "window_end", label: "A demand window ends" },
  { type: "time", label: "At a set time" },
  { type: "sun", label: "At sunrise or sunset" },
  { type: "price_above", label: "Price goes above a level" },
  { type: "temperature", label: "A temperature crosses a level" },
];

type FieldsProps = { model: EditorModel; windows: DemandWindow[] };

function triggerSetter(model: EditorModel) {
  const { form, setForm } = model;
  return (patch: Partial<TriggerForm>) => setForm({ ...form, trigger: { ...form.trigger, ...patch } });
}

function WindowField({ model, windows }: FieldsProps) {
  const errorId = useId();
  const change = triggerSetter(model);
  const { windowId } = model.form.trigger;
  const error = model.errors["trigger.windowId"];
  const listed = windows.some((w) => String(w.id) === windowId);
  return (
    <div className="space-y-1">
      <label className={labelClass}>
        Demand window
        <select value={windowId} onChange={(e) => change({ windowId: e.target.value })} className={inputClass} {...invalidProps(errorId, error)}>
          {windowId === "" && <option value="">Pick a window</option>}
          {windowId !== "" && !listed && <option value={windowId}>Window {windowId} (not on the current tariff)</option>}
          {windows.map((w) => (
            <option key={w.id} value={String(w.id)}>{windowLabel(w)}</option>
          ))}
        </select>
      </label>
      <p className={hintClass}>Windows come from this site's tariff.</p>
      <FieldError id={errorId} message={error} />
    </div>
  );
}

function TimeFields({ model }: { model: EditorModel }) {
  const errorId = useId();
  const change = triggerSetter(model);
  const { time, days } = model.form.trigger;
  const error = model.errors["trigger.time"];
  const daysError = model.errors["trigger.days"];
  const daysErrorId = `${errorId}-days`;
  return (
    <div className="space-y-3">
      <label className={labelClass}>
        Time (HH:MM, site time)
        <input value={time} onChange={(e) => change({ time: e.target.value })} placeholder="16:00" inputMode="numeric" className={`${inputClass} max-w-32`} {...invalidProps(errorId, error)} />
      </label>
      <FieldError id={errorId} message={error} />
      <CheckboxGroup
        legend="Days"
        options={DAY_OPTIONS}
        selection={{ selected: days, onChange: (next) => change({ days: next }), describedBy: daysError ? daysErrorId : undefined }}
      />
      <FieldError id={daysErrorId} message={daysError} />
    </div>
  );
}

function SunFields({ model }: { model: EditorModel }) {
  const errorId = useId();
  const change = triggerSetter(model);
  const { event, offset, days } = model.form.trigger;
  const error = model.errors["trigger.offset"];
  const daysError = model.errors["trigger.days"];
  const daysErrorId = `${errorId}-days`;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className={labelClass}>
          Event
          <select value={event} onChange={(e) => change({ event: e.target.value === "sunrise" ? "sunrise" : "sunset" })} className={inputClass}>
            <option value="sunrise">Sunrise</option>
            <option value="sunset">Sunset</option>
          </select>
        </label>
        <label className={labelClass}>
          Minutes after (negative is before)
          <input type="number" step={1} min={-MAX_OFFSET_MINUTES} max={MAX_OFFSET_MINUTES} value={offset} onChange={(e) => change({ offset: e.target.value })} className={`${inputClass} w-28`} {...invalidProps(errorId, error)} />
        </label>
      </div>
      <FieldError id={errorId} message={error} />
      <CheckboxGroup
        legend="Days"
        options={DAY_OPTIONS}
        selection={{ selected: days, onChange: (next) => change({ days: next }), describedBy: daysError ? daysErrorId : undefined }}
      />
      <FieldError id={daysErrorId} message={daysError} />
      <SunNote locationSet={model.locationSet} />
    </div>
  );
}

/** Says where sun times come from while the site has no location of its own. */
export function SunNote({ locationSet }: { locationSet: boolean }) {
  if (locationSet) return <p className={hintClass}>Sun times come from the site's location.</p>;
  return <p className={hintClass}>Sun times are estimated from the site's time zone. Set the location on the site's General tab for exact times.</p>;
}

function RearmField({ model, side }: { model: EditorModel; side: string }) {
  const errorId = useId();
  const change = triggerSetter(model);
  const error = model.errors["trigger.rearm"];
  return (
    <div className="space-y-1">
      <label className={labelClass}>
        Can fire again once it is back {side}
        <input type="number" step="any" value={model.form.trigger.rearm} onChange={(e) => change({ rearm: e.target.value, rearmAuto: false })} className={`${inputClass} max-w-40`} {...invalidProps(errorId, error)} />
      </label>
      <p className={hintClass}>Leave blank to allow it as soon as the level is no longer passed. A value further back stops the schedule starting and stopping as the reading hovers around the level.</p>
      <FieldError id={errorId} message={error} />
    </div>
  );
}

function PriceField({ model }: { model: EditorModel }) {
  const errorId = useId();
  const change = triggerSetter(model);
  const error = model.errors["trigger.price"];
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <label className={labelClass}>
          Price above (c/kWh)
          <input type="number" step="any" value={model.form.trigger.price} onChange={(e) => change({ price: e.target.value })} className={`${inputClass} max-w-40`} {...invalidProps(errorId, error)} />
        </label>
        <FieldError id={errorId} message={error} />
      </div>
      <RearmField model={model} side="at or below (c/kWh)" />
    </div>
  );
}

function TemperatureFields({ model }: { model: EditorModel }) {
  const errorId = useId();
  const change = triggerSetter(model);
  const { reading, op, level } = model.form.trigger;
  const error = model.errors["trigger.level"];
  const { rearmAuto } = model.form.trigger;
  // While the re-arm value is still the suggestion, it follows the level and the direction.
  const follow = (next: { level?: string; op?: "gt" | "lt" }): Partial<TriggerForm> =>
    rearmAuto ? { ...next, rearm: suggestRearm(next.level ?? level, next.op ?? op) } : next;
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <div className="flex flex-wrap items-end gap-3">
          <label className={labelClass}>
            Temperature
            <select value={reading} onChange={(e) => change({ reading: e.target.value === "indoor_temp_c" ? "indoor_temp_c" : "outdoor_temp_c" })} className={inputClass}>
              <option value="outdoor_temp_c">Outside</option>
              <option value="indoor_temp_c">Inside</option>
            </select>
          </label>
          <label className={labelClass}>
            Crosses
            <select value={op} onChange={(e) => change(follow({ op: e.target.value === "lt" ? "lt" : "gt" }))} className={inputClass}>
              <option value="gt">Rises above</option>
              <option value="lt">Falls below</option>
            </select>
          </label>
          <label className={labelClass}>
            Level (C)
            <input type="number" step="any" value={level} onChange={(e) => change(follow({ level: e.target.value }))} className={`${inputClass} w-28`} {...invalidProps(errorId, error)} />
          </label>
        </div>
        <FieldError id={errorId} message={error} />
      </div>
      <RearmField model={model} side={op === "gt" ? "at or below (C)" : "at or above (C)"} />
    </div>
  );
}

function TriggerSelect({ model, windows }: FieldsProps) {
  const { form, setForm } = model;
  const noWindows = windows.length === 0;
  return (
    <label className={labelClass}>
      Trigger
      <select value={form.trigger.type} onChange={(e) => setForm(setTriggerType(form, e.target.value as TriggerType))} className={inputClass}>
        {TRIGGERS.map((t) => (
          <option key={t.type} value={t.type} disabled={noWindows && isWindowTrigger(t.type) && t.type !== form.trigger.type}>
            {t.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function WhenStep({ model, windows }: FieldsProps) {
  const { type } = model.form.trigger;
  return (
    <Step number={2} title="When">
      <TriggerSelect model={model} windows={windows} />
      {windows.length === 0 && <p className={hintClass}>This site's tariff has no demand windows.</p>}
      {isWindowTrigger(type) && <WindowField model={model} windows={windows} />}
      {type === "time" && <TimeFields model={model} />}
      {type === "sun" && <SunFields model={model} />}
      {type === "price_above" && <PriceField model={model} />}
      {type === "temperature" && <TemperatureFields model={model} />}
    </Step>
  );
}
