import { useId } from "react";
import { formatUnit, invalidProps } from "./editorModel";
import { addCondition, type ConditionRow, isSwitchedOn, pointAtReading, type ReadingChoice, setSwitch } from "./scheduleForm";
import { targetOffered, type FormErrors } from "./scheduleValidation";
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

const NO_TARGET = [{ value: "", label: "No target" }];

/** The reading's targets, with a stored target it no longer offers listed first so the select shows what is held.
 * A reading without targets offers "No target" next to such a stale one. Null when there is nothing to pick. */
function targetChoices(row: ConditionRow, reading: ReadingChoice | undefined): { value: string; label: string }[] | null {
  const stale = targetOffered(row, reading) ? [] : [{ value: row.target ?? "", label: `${row.target} (no longer offered)` }];
  if (!reading?.targets && stale.length === 0) return null;
  return [...stale, ...(reading?.targets ?? NO_TARGET)];
}

function TargetSelect({ model, reading }: { model: RowModel; reading: ReadingChoice | undefined }) {
  const { row, number, onChange } = model;
  const choices = targetChoices(row, reading);
  if (!choices) return null;
  return (
    <select aria-label={`Target ${number}`} value={row.target ?? ""} onChange={(e) => onChange({ ...row, target: e.target.value || null })} className={compactInputClass}>
      {choices.map((t) => (
        <option key={t.value} value={t.value}>{t.label}</option>
      ))}
    </select>
  );
}

function ReadingSelect({ model }: { model: RowModel }) {
  const { row, number, readings, onChange } = model;
  const known = readings.some((r) => r.id === row.reading);
  return (
    <select aria-label={`Reading ${number}`} value={row.reading} onChange={(e) => onChange(pointAtReading({ ...row, reading: e.target.value }, readings.find((r) => r.id === e.target.value)))} className={compactInputClass}>
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
        {reading?.type === "bool" ? (
          <>
            <span>is</span>
            <select aria-label={`State ${number}`} value={isSwitchedOn(row) ? "on" : "off"} onChange={(e) => onChange(setSwitch(row, e.target.value === "on"))} className={compactInputClass}>
              <option value="on">On</option>
              <option value="off">Off</option>
            </select>
          </>
        ) : (
          <>
            <select aria-label={`Comparison ${number}`} value={row.op} onChange={(e) => onChange({ ...row, op: e.target.value === "lt" ? "lt" : "gt" })} className={compactInputClass}>
              <option value="gt">is above</option>
              <option value="lt">is below</option>
            </select>
            <input type="number" step="any" aria-label={`Value ${number}`} value={row.value} onChange={(e) => onChange({ ...row, value: e.target.value })} className={`${compactInputClass} w-24`} {...invalidProps(errorId, error)} />
            <span className="text-ink-muted">{formatUnit(reading?.unit ?? null)}</span>
          </>
        )}
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

/** A list of "reading is above/below value" rows. Each row has a tick box; an unticked row is kept
 * and saved switched off, and only ticked rows count. */
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
