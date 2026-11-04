import { useId } from "react";
import { formatUnit, invalidProps } from "./editorModel";
import type { FormParam } from "./scheduleForm";
import { FieldError } from "./Step";
import { hintClass, inputClass, labelClass } from "./styles";
import type { ParamDecl } from "./types";

export interface ParamInput {
  value: FormParam | undefined;
  onChange: (value: FormParam) => void;
  error: string | undefined;
}

type FieldProps = { param: ParamDecl; input: ParamInput; errorId: string };

function rangeHint(param: ParamDecl): string | null {
  const unit = param.unit ? ` ${formatUnit(param.unit)}` : "";
  if (param.min !== null && param.max !== null) return `${param.min} to ${param.max}${unit}`;
  if (param.min !== null) return `At least ${param.min}${unit}`;
  return param.max !== null ? `At most ${param.max}${unit}` : null;
}

function NumberField({ param, input, errorId }: FieldProps) {
  const hint = rangeHint(param);
  const label = param.unit ? `${param.label} (${formatUnit(param.unit)})` : param.label;
  return (
    <label className={labelClass}>
      {label}
      <input
        type="number"
        step="any"
        min={param.min ?? undefined}
        max={param.max ?? undefined}
        value={input.value?.kind === "number" ? input.value.text : ""}
        onChange={(e) => input.onChange({ kind: "number", text: e.target.value })}
        className={`${inputClass} max-w-40`}
        {...invalidProps(errorId, input.error)}
      />
      {hint && <span className={`block ${hintClass}`}>{hint}</span>}
    </label>
  );
}

function ChoiceField({ param, input, errorId }: FieldProps) {
  const value = input.value?.kind === "value" && typeof input.value.value === "string" ? input.value.value : "";
  return (
    <label className={labelClass}>
      {param.label}
      <select value={value} onChange={(e) => input.onChange({ kind: "value", value: e.target.value })} className={inputClass} {...invalidProps(errorId, input.error)}>
        {param.options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </label>
  );
}

function BoolField({ param, input }: FieldProps) {
  const checked = input.value?.kind === "value" && input.value.value === true;
  return (
    <label className="flex items-center gap-2 text-sm text-ink">
      <input type="checkbox" className="accent-solar" checked={checked} onChange={(e) => input.onChange({ kind: "value", value: e.target.checked })} />
      {param.label}
    </label>
  );
}

function MultiChoiceField({ param, input, errorId }: FieldProps) {
  const picked = input.value?.kind === "value" && Array.isArray(input.value.value) ? input.value.value : [];
  const order = param.options.map((o) => o.value);
  function toggle(value: string) {
    const next = picked.includes(value) ? picked.filter((v) => v !== value) : [...picked, value];
    input.onChange({ kind: "value", value: order.filter((v) => next.includes(v)) });
  }
  return (
    <fieldset className="flex flex-wrap gap-x-4 gap-y-2 text-sm" {...invalidProps(errorId, input.error)}>
      <legend className="mb-1.5 text-ink">{param.label}</legend>
      {param.options.map((o) => (
        <label key={o.value} className="flex items-center gap-1.5 text-ink">
          <input type="checkbox" className="accent-solar" checked={picked.includes(o.value)} onChange={() => toggle(o.value)} />
          {o.label}
        </label>
      ))}
    </fieldset>
  );
}

const FIELDS = { number: NumberField, choice: ChoiceField, bool: BoolField, multi_choice: MultiChoiceField };

/** One declared action param, drawn by its type. */
export function ParamField({ param, input }: { param: ParamDecl; input: ParamInput }) {
  const errorId = useId();
  const Field = FIELDS[param.type];
  return (
    <div className="space-y-1">
      <Field param={param} input={input} errorId={errorId} />
      <FieldError id={errorId} message={input.error} />
    </div>
  );
}
