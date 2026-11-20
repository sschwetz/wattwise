import { type FormEvent, useState } from "react";
import { MutationError, inputClass, mutedClass, outlineButtonClass, primaryButtonClass } from "./adminUi";
import { useSaveProviderSettings, type MailProvider, type ProviderField } from "./useAdminMail";

type Edits = Record<string, string>;
type SetEdit = (name: string, value: string | null) => void;

interface FieldProps {
  field: ProviderField;
  edit: string | undefined;
  disabled: boolean;
  onEdit: SetEdit;
}

function labelText(field: ProviderField): string {
  return field.required ? `${field.label} (required)` : field.label;
}

function SecretField({ field, edit, disabled, onEdit }: FieldProps) {
  const [editing, setEditing] = useState(false);
  if (!editing) {
    return (
      <div className="space-y-1">
        <p className="text-sm text-ink">
          {labelText(field)}: {field.is_set ? "set" : "not set"}
        </p>
        <button type="button" disabled={disabled} onClick={() => setEditing(true)} className={outlineButtonClass}>
          Change
        </button>
      </div>
    );
  }
  function cancel() {
    onEdit(field.name, null);
    setEditing(false);
  }
  return (
    <div className="space-y-1">
      <label className="flex flex-col gap-1 text-sm text-ink">
        {`New ${labelText(field)}`}
        <input
          type="password"
          value={edit ?? ""}
          disabled={disabled}
          onChange={(e) => onEdit(field.name, e.target.value)}
          autoComplete="off"
          spellCheck={false}
          className={inputClass}
        />
      </label>
      <button type="button" disabled={disabled} onClick={cancel} className={outlineButtonClass}>
        Cancel
      </button>
    </div>
  );
}

function TextField({ field, edit, disabled, onEdit }: FieldProps) {
  return (
    <label className="flex flex-col gap-1 text-sm text-ink">
      {labelText(field)}
      <input
        type="text"
        value={edit ?? field.value ?? ""}
        disabled={disabled}
        onChange={(e) => onEdit(field.name, e.target.value)}
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

export function ProviderFieldsForm({ provider }: { provider: MailProvider }) {
  const [edits, setEdits] = useState<Edits>({});
  const save = useSaveProviderSettings(provider.provider);
  const changed = changedSettings(provider.fields, edits);
  const onEdit: SetEdit = (name, value) => setEdits((prev) => withEdit(prev, name, value));

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    save.mutate(changed, { onSuccess: () => setEdits({}) });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      {provider.fields.length === 0 && <p className={mutedClass}>This provider has no settings.</p>}
      {provider.fields.map((field) => {
        const Field = field.secret ? SecretField : TextField;
        return <Field key={field.name} field={field} edit={edits[field.name]} disabled={save.isPending} onEdit={onEdit} />;
      })}
      <MutationError error={save.error} fallback="Could not save the settings. Please try again." />
      <button type="submit" disabled={save.isPending || Object.keys(changed).length === 0} className={primaryButtonClass}>
        Save
      </button>
    </form>
  );
}
