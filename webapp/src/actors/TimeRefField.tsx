import { invalidProps } from "./editorModel";
import { defaultClockRef, MAX_OFFSET_MINUTES, sunRef, type TimeRefForm } from "./scheduleForm";
import { compactInputClass } from "./styles";

export interface TimeRefFieldProps {
  /** Names the controls for screen readers, such as "Start". */
  label: string;
  value: TimeRefForm;
  onChange: (value: TimeRefForm) => void;
  /** The message shown for this field and the id of the element that shows it. */
  error?: { id: string; message: string | undefined };
}

type Choice = "clock" | "sunrise" | "sunset";

const choiceOf = (ref: TimeRefForm): Choice => (ref.kind === "clock" ? "clock" : ref.event);

function pick(choice: Choice, current: TimeRefForm): TimeRefForm {
  if (choice === "clock") return current.kind === "clock" ? current : defaultClockRef("");
  return current.kind === "sun" ? { ...current, event: choice } : sunRef(choice);
}

/** A clock time (HH:MM, site time), or sunrise or sunset with an offset in minutes, negative for before. */
export function TimeRefField({ label, value, onChange, error }: TimeRefFieldProps) {
  const flag = invalidProps(error?.id ?? "", error?.message);
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <select aria-label={`${label} type`} value={choiceOf(value)} onChange={(e) => onChange(pick(e.target.value as Choice, value))} className={compactInputClass}>
        <option value="clock">Clock time</option>
        <option value="sunrise">Sunrise</option>
        <option value="sunset">Sunset</option>
      </select>
      {value.kind === "clock" ? (
        <input aria-label={`${label} time`} value={value.time} onChange={(e) => onChange({ kind: "clock", time: e.target.value })} placeholder="18:00" inputMode="numeric" className={`${compactInputClass} w-20`} {...flag} />
      ) : (
        <>
          <input
            type="number"
            step={1}
            min={-MAX_OFFSET_MINUTES}
            max={MAX_OFFSET_MINUTES}
            aria-label={`${label} offset`}
            value={value.offset}
            onChange={(e) => onChange({ ...value, offset: e.target.value })}
            className={`${compactInputClass} w-20`}
            {...flag}
          />
          <span className="text-ink-muted">min</span>
        </>
      )}
    </span>
  );
}
