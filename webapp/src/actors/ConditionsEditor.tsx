import { useId } from "react";
import { formatUnit, invalidProps } from "./editorModel";
import { addCondition, type ConditionRow, type FormErrors, type ReadingChoice } from "./scheduleForm";
import { FieldError } from "./Step";
import { compactInputClass, hintClass, secondaryButtonClass } from "./styles";

export interface ConditionsModel {
  legend: string;
  /** The step heading already names the list, so the legend is for screen readers only. */
  legendHidden?: boolean;
  hint: string;
  rows: ConditionRow[];
  onChange: (rows: ConditionRow[]) => void;
  readings: ReadingChoice[];
  errors: FormErrors;
  /** The key prefix validateForm uses for these rows, such as "conditions". */
  errorPrefix: string;
  canEdit: boolean;
}

interface RowModel {
  row: ConditionRow;
  number: number;
  readings: ReadingChoice[];
  onChange: (row: ConditionRow) => void;
  onRemove: () => void;
  error: string | undefined;
  canEdit: boolean;
}

function changeReading(row: ConditionRow, id: string, readings: ReadingChoice[]): ConditionRow {
  const reading = readings.find((r) => r.id === id);
  return { ...row, reading: id, target: reading?.targets?.[0]?.value ?? null };
}

function TargetSelect({ model, reading }: { model: RowModel; reading: ReadingChoice | undefined }) {
  const { row, number, onChange } = model;
  if (!reading?.targets) return null;
  return (
    <select aria-label={`Target ${number}`} value={row.target ?? ""} onChange={(e) => onChange({ ...row, target: e.target.value })} className={compactInputClass}>
      {reading.targets.map((t) => (
        <option key={t.value} value={t.value}>{t.label}</option>
      ))}
    </select>
  );
}

function ReadingSelect({ model }: { model: RowModel }) {
  const { row, number, readings, onChange } = model;
  const known = readings.some((r) => r.id === row.reading);
  return (
    <select aria-label={`Reading ${number}`} value={row.reading} onChange={(e) => onChange(changeReading(row, e.target.value, readings))} className={compactInputClass}>
      {!known && <option value={row.reading}>{row.reading} (not available)</option>}
      {readings.map((r) => (
        <option key={r.id} value={r.id}>{r.label}</option>
      ))}
    </select>
  );
}

function ConditionLine({ model }: { model: RowModel }) {
  const { row, number, readings, onChange, onRemove, error, canEdit } = model;
  const errorId = useId();
  const reading = readings.find((r) => r.id === row.reading);
  return (
    <li className="space-y-1">
      <div className="flex flex-wrap items-center gap-2 text-sm text-ink">
        <input type="checkbox" aria-label={`Use condition ${number}`} className="accent-solar" checked={row.use} onChange={(e) => onChange({ ...row, use: e.target.checked })} />
        <ReadingSelect model={model} />
        <TargetSelect model={model} reading={reading} />
        <select aria-label={`Comparison ${number}`} value={row.op} onChange={(e) => onChange({ ...row, op: e.target.value === "lt" ? "lt" : "gt" })} className={compactInputClass}>
          <option value="gt">is above</option>
          <option value="lt">is below</option>
        </select>
        <input type="number" step="any" aria-label={`Value ${number}`} value={row.value} onChange={(e) => onChange({ ...row, value: e.target.value })} className={`${compactInputClass} w-24`} {...invalidProps(errorId, error)} />
        <span className="text-ink-muted">{formatUnit(reading?.unit ?? null)}</span>
        {canEdit && (
          <button type="button" aria-label={`Remove condition ${number}`} onClick={onRemove} className="rounded-md px-2 py-1 text-sm text-ink-muted underline hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60">
            Remove
          </button>
        )}
      </div>
      <FieldError id={errorId} message={error} />
    </li>
  );
}

/** A list of "reading is above/below value" rows. Each row has a tick box; unticked rows are not saved. */
export function ConditionsEditor({ model }: { model: ConditionsModel }) {
  const { legend, legendHidden, hint, rows, onChange, readings, errors, errorPrefix, canEdit } = model;
  const replace = (key: number, next: ConditionRow) => onChange(rows.map((r) => (r.key === key ? next : r)));
  return (
    <fieldset className="space-y-2">
      <legend className={legendHidden ? "sr-only" : "text-sm font-medium text-ink"}>{legend}</legend>
      {rows.length > 0 && (
        <ul className="space-y-2">
          {rows.map((row, i) => (
            <ConditionLine
              key={row.key}
              model={{
                row, number: i + 1, readings, canEdit,
                onChange: (next) => replace(row.key, next),
                onRemove: () => onChange(rows.filter((r) => r.key !== row.key)),
                error: errors[`${errorPrefix}.${row.key}`],
              }}
            />
          ))}
        </ul>
      )}
      <p className={hintClass}>{hint}</p>
      {canEdit && (
        <button type="button" onClick={() => onChange(addCondition(rows, readings))} className={secondaryButtonClass}>
          Add condition
        </button>
      )}
    </fieldset>
  );
}
