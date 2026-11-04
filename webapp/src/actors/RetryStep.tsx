import { useId } from "react";
import { type EditorModel, invalidProps } from "./editorModel";
import type { RetryForm } from "./scheduleForm";
import { FieldError, Step } from "./Step";
import { inputClass, labelClass } from "./styles";

const FIELDS: { key: keyof RetryForm; label: string; min: number }[] = [
  { key: "maxAttempts", label: "Attempts", min: 1 },
  { key: "intervalSeconds", label: "Seconds between tries", min: 5 },
  { key: "giveUpMinutes", label: "Give up after (minutes)", min: 1 },
];

function RetryField({ model, field }: { model: EditorModel; field: (typeof FIELDS)[number] }) {
  const errorId = useId();
  const { form, setForm, errors } = model;
  const error = errors[`retry.${field.key}`];
  return (
    <div className="space-y-1">
      <label className={labelClass}>
        {field.label}
        <input
          type="number"
          min={field.min}
          step={1}
          value={form.retry[field.key]}
          onChange={(e) => setForm({ ...form, retry: { ...form.retry, [field.key]: e.target.value } })}
          className={inputClass}
          {...invalidProps(errorId, error)}
        />
      </label>
      <FieldError id={errorId} message={error} />
    </div>
  );
}

export function RetryStep({ model }: { model: EditorModel }) {
  const { form, setForm } = model;
  return (
    <Step number={6} title="If it fails">
      <div className="grid gap-3 sm:grid-cols-3">
        {FIELDS.map((field) => (
          <RetryField key={field.key} model={model} field={field} />
        ))}
      </div>
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" className="accent-solar" checked={form.notifyEmail} onChange={(e) => setForm({ ...form, notifyEmail: e.target.checked })} />
        Email me if it still fails
      </label>
    </Step>
  );
}
