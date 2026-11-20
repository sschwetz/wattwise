import { type FormEvent, useState } from "react";
import { inputClass } from "../components/formStyles";
import { MutationError, mutedClass, outlineButtonClass, primaryButtonClass } from "./adminUi";
import { useSaveProviderSettings, type MailProvider, type ProviderField } from "./useAdminMail";

type Edits = Record<string, string>;
type SetEdit = (name: string, value: string | null) => void;

/** What a field needs from the form: its pending edit, whether the form is busy, and how to change the edit. */
interface FieldControl {
  edit: string | undefined;
  disabled: boolean;
  onEdit: SetEdit;
}

interface FieldProps {
  field: ProviderField;
  control: FieldControl;
}

function labelText(field: ProviderField): string {
  return field.required ? `${field.label} (required)` : field.label;
}

function SecretDisplay({ field, control, onChange }: FieldProps & { onChange: () => void }) {
  return (
    <div className="space-y-1">
      <p className="text-sm text-ink">
        {labelText(field)}: {field.is_set ? "set" : "not set"}
      </p>
      <button type="button" disabled={control.disabled} onClick={onChange} className={outlineButtonClass}>
        Change
      </button>
    </div>
  );
}

function SecretEditor({ field, control, onCancel }: FieldProps & { onCancel: () => void }) {
  return (
    <div className="space-y-1">
      <label className="flex flex-col gap-1 text-sm text-ink">
        {`New ${labelText(field)}`}
        <input
          type="password"
          value={control.edit ?? ""}
          disabled={control.disabled}
          onChange={(e) => control.onEdit(field.name, e.target.value)}
          autoComplete="off"
          spellCheck={false}
          className={inputClass}
        />
      </label>
      <button type="button" disabled={control.disabled} onClick={onCancel} className={outlineButtonClass}>
        Cancel
      </button>
    </div>
  );
}

function SecretField({ field, control }: FieldProps) {
  const [editing, setEditing] = useState(false);
  if (!editing) return <SecretDisplay field={field} control={control} onChange={() => setEditing(true)} />;
  function cancel() {
    control.onEdit(field.name, null);
    setEditing(false);
  }
  return <SecretEditor field={field} control={control} onCancel={cancel} />;
}

function TextField({ field, control }: FieldProps) {
  return (
    <label className="flex flex-col gap-1 text-sm text-ink">
      {labelText(field)}
      <input
        type="text"
        value={control.edit ?? field.value ?? ""}
        disabled={control.disabled}
        onChange={(e) => control.onEdit(field.name, e.target.value)}
        autoComplete="off"
        className={inputClass}
      />
    </label>
  );
}

/** The settings to send: non-secret fields whose text differs from the stored value, and secrets the admin typed. */
function changedSettings(fields: ProviderField[], edits: Edits): Edits {
  const out: Edits = {};
  for (const f of fields) {
    const edit = edits[f.name];
    if (edit === undefined) continue;
    if (f.secret ? edit !== "" : edit !== (f.value ?? "")) out[f.name] = edit;
  }
  return out;
}

function withEdit(edits: Edits, name: string, value: string | null): Edits {
  const next = { ...edits };
  if (value === null) delete next[name];
  else next[name] = value;
  return next;
}

/** The pending edits, plus a counter that changes on reset so a secret input returns to its "set" display. */
function useFieldEdits() {
  const [edits, setEdits] = useState<Edits>({});
  const [resets, setResets] = useState(0);
  const onEdit: SetEdit = (name, value) => setEdits((prev) => withEdit(prev, name, value));
  const reset = () => {
    setEdits({});
    setResets((n) => n + 1);
  };
  return { edits, resets, onEdit, reset };
}

function FieldRow({ field, control }: FieldProps) {
  return field.secret ? <SecretField field={field} control={control} /> : <TextField field={field} control={control} />;
}

export function ProviderFieldsForm({ provider }: { provider: MailProvider }) {
  const { edits, resets, onEdit, reset } = useFieldEdits();
  const save = useSaveProviderSettings(provider.provider);
  const changed = changedSettings(provider.fields, edits);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    save.mutate(changed, { onSuccess: reset });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      {provider.fields.length === 0 && <p className={mutedClass}>This provider has no settings.</p>}
      {provider.fields.map((f) => (
        <FieldRow key={`${f.name}:${resets}`} field={f} control={{ edit: edits[f.name], disabled: save.isPending, onEdit }} />
      ))}
      <MutationError error={save.error} fallback="Could not save the settings. Please try again." />
      <button type="submit" disabled={save.isPending || Object.keys(changed).length === 0} className={primaryButtonClass}>
        Save
      </button>
    </form>
  );
}
