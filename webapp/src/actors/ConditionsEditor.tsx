import { useId } from "react";
import type { DemandWindow } from "../billing/useBilling";
import { SHORT_DAYS, SHORT_MONTHS } from "../sources/rateWindowUi";
import { changeKind, currentWeekParity } from "./calendarConditions";
import { windowLabel } from "./demandWindows";
import { formatUnit, invalidProps } from "./editorModel";
import { addCondition, type ConditionKind, type ConditionRow, isSwitchedOn, type MembershipRow, pointAtReading, type ReadingChoice, type ReadingRow, setSwitch, type WeekRow, type WindowRow } from "./scheduleForm";
import { targetOffered, type FormErrors } from "./scheduleValidation";
import { FieldError } from "./Step";
import { compactInputClass, hintClass, secondaryButtonClass } from "./styles";

export interface CalendarModel {
  windows: DemandWindow[];
  timezone: string;
}

export interface ConditionsModel {
  legend: string;
  /** The step heading already names the list, so the legend is for screen readers only. */
  legendHidden?: boolean;
  hint: string;
  rows: ConditionRow[];
  onChange: (rows: ConditionRow[]) => void;
  readings: ReadingChoice[];
  /** What the calendar rows need: the windows to pick from and the site's time zone. */
  calendar: CalendarModel;
  errors: FormErrors;
  /** The key prefix validateForm uses for these rows, such as "conditions". */
  errorPrefix: string;
  canEdit: boolean;
}

interface RowModel {
  row: ConditionRow;
  number: number;
  readings: ReadingChoice[];
  calendar: CalendarModel;
  onChange: (row: ConditionRow) => void;
  onRemove: () => void;
  error: string | undefined;
  canEdit: boolean;
}

/** A row model narrowed to one kind of row. */
type KindModel<R extends ConditionRow> = Omit<RowModel, "row"> & { row: R; errorId: string };

const CALENDAR_KINDS: { kind: ConditionKind; label: string }[] = [
  { kind: "months", label: "Month" },
  { kind: "days", label: "Day of week" },
  { kind: "week", label: "Week" },
  { kind: "demand_window", label: "Demand window" },
];

const NO_TARGET = [{ value: "", label: "No target" }];

/** The reading's targets, with a stored target it no longer offers listed first so the select shows what is held.
 * A reading without targets offers "No target" next to such a stale one. Null when there is nothing to pick. */
function targetChoices(row: ReadingRow, reading: ReadingChoice | undefined): { value: string; label: string }[] | null {
  const stale = targetOffered(row, reading) ? [] : [{ value: row.target ?? "", label: `${row.target} (no longer offered)` }];
  if (!reading?.targets && stale.length === 0) return null;
  return [...stale, ...(reading?.targets ?? NO_TARGET)];
}

function TargetSelect({ model, reading }: { model: KindModel<ReadingRow>; reading: ReadingChoice | undefined }) {
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

/** The first select: what the row tests. A reading, or one of the calendar kinds. */
function KindSelect({ model }: { model: RowModel }) {
  const { row, number, readings, calendar, onChange } = model;
  const known = row.kind !== "reading" || readings.some((r) => r.id === row.reading);
  const defaults = () => ({ readings, parity: currentWeekParity(new Date(), calendar.timezone) });
  function pick(value: string) {
    const calendarKind = CALENDAR_KINDS.find((k) => k.kind === value);
    if (calendarKind) return onChange(changeKind(row, calendarKind.kind, defaults()));
    const base: ReadingRow = row.kind === "reading" ? row : { key: row.key, use: row.use, kind: "reading", reading: "", op: "gt", value: "", target: null };
    onChange(pointAtReading({ ...base, reading: value }, readings.find((r) => r.id === value)));
  }
  return (
    <select aria-label={`Reading ${number}`} value={row.kind === "reading" ? row.reading : row.kind} onChange={(e) => pick(e.target.value)} className={compactInputClass}>
      {!known && row.kind === "reading" && <option value={row.reading}>{row.reading} (not available)</option>}
      {readings.map((r) => (
        <option key={r.id} value={r.id}>{r.label}</option>
      ))}
      <optgroup label="Calendar">
        {CALENDAR_KINDS.map((k) => (
          <option key={k.kind} value={k.kind}>{k.label}</option>
        ))}
      </optgroup>
    </select>
  );
}

function ReadingControls({ model }: { model: KindModel<ReadingRow> }) {
  const { row, number, readings, onChange, error, errorId } = model;
  const reading = readings.find((r) => r.id === row.reading);
  return (
    <>
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
    </>
  );
}

const MEMBERSHIP = {
  months: { names: SHORT_MONTHS, first: 1, comparison: "Month comparison" },
  days: { names: SHORT_DAYS, first: 0, comparison: "Day comparison" },
};

/** "is / is not" and one toggle button per month or day. */
function MembershipControls({ model }: { model: KindModel<MembershipRow> }) {
  const { row, number, onChange } = model;
  const { names, first, comparison } = MEMBERSHIP[row.kind];
  const toggle = (value: number) =>
    onChange({ ...row, values: row.values.includes(value) ? row.values.filter((v) => v !== value) : [...row.values, value] });
  return (
    <>
      <select aria-label={`${comparison} ${number}`} value={row.op} onChange={(e) => onChange({ ...row, op: e.target.value === "not_in" ? "not_in" : "in" })} className={compactInputClass}>
        <option value="in">is</option>
        <option value="not_in">is not</option>
      </select>
      <span className="flex flex-wrap gap-1">
        {names.map((name, i) => {
          const pressed = row.values.includes(first + i);
          return (
            <button key={name} type="button" aria-pressed={pressed} onClick={() => toggle(first + i)} className={`rounded-md border px-2 py-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 ${pressed ? "border-solar bg-solar/20 text-ink" : "border-line text-ink-muted"}`}>
              {name}
            </button>
          );
        })}
      </span>
    </>
  );
}

function WeekControls({ model }: { model: KindModel<WeekRow> }) {
  const { row, number, onChange } = model;
  return (
    <>
      <span>is</span>
      <select aria-label={`Week parity ${number}`} value={row.parity} onChange={(e) => onChange({ ...row, parity: e.target.value === "even" ? "even" : "odd" })} className={compactInputClass}>
        <option value="odd">odd</option>
        <option value="even">even</option>
      </select>
    </>
  );
}

function WindowControls({ model }: { model: KindModel<WindowRow> }) {
  const { row, number, calendar, onChange } = model;
  const known = row.windowId === "" || calendar.windows.some((w) => String(w.id) === row.windowId);
  return (
    <>
      <select aria-label={`Window state ${number}`} value={row.op} onChange={(e) => onChange({ ...row, op: e.target.value === "active" ? "active" : "not_active" })} className={compactInputClass}>
        <option value="not_active">is not</option>
        <option value="active">is</option>
      </select>
      <span>active:</span>
      <select aria-label={`Window ${number}`} value={row.windowId} onChange={(e) => onChange({ ...row, windowId: e.target.value })} className={compactInputClass}>
        <option value="">Any demand window</option>
        {!known && <option value={row.windowId}>{row.windowId} (no longer available)</option>}
        {calendar.windows.map((w) => (
          <option key={w.id} value={String(w.id)}>{windowLabel(w)}</option>
        ))}
      </select>
    </>
  );
}

function RowControls({ model }: { model: RowModel & { errorId: string } }) {
  const { row } = model;
  switch (row.kind) {
    case "reading":
      return <ReadingControls model={{ ...model, row }} />;
    case "months":
    case "days":
      return <MembershipControls model={{ ...model, row }} />;
    case "week":
      return <WeekControls model={{ ...model, row }} />;
    case "demand_window":
      return <WindowControls model={{ ...model, row }} />;
  }
}

function ConditionLine({ model }: { model: RowModel }) {
  const { number, onRemove, error, canEdit, row, onChange } = model;
  const errorId = useId();
  return (
    <li className="space-y-1">
      <div className="flex flex-wrap items-center gap-2 text-sm text-ink">
        <input type="checkbox" aria-label={`Use condition ${number}`} className="accent-solar" checked={row.use} onChange={(e) => onChange({ ...row, use: e.target.checked })} />
        <KindSelect model={model} />
        <RowControls model={{ ...model, errorId }} />
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

/** A list of condition rows: a reading above or below a value, a month, day, week or demand window test. Each row has a tick box; an unticked row is kept
 * and saved switched off, and only ticked rows count. */
export function ConditionsEditor({ model }: { model: ConditionsModel }) {
  const { legend, legendHidden, hint, rows, onChange, readings, calendar, errors, errorPrefix, canEdit } = model;
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
                row, number: i + 1, readings, calendar, canEdit,
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
