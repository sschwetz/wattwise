import { useState } from "react";
import { MutationError, mutedClass, outlineButtonClass, primaryButtonClass } from "./adminUi";
import { useResetJob, useSaveJob, type AdminJob, type JobTimingInput } from "./useAdminJobs";

const inputClass =
  "rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60";

const pad = (n: number | null) => String(n ?? 0).padStart(2, "0");

/** The edited timing as text: minutes for an interval job, HH:MM for a daily job. */
function initialValue(job: AdminJob): string {
  return job.kind === "daily" ? `${pad(job.timing.hour)}:${pad(job.timing.minute)}` : String(job.timing.interval_minutes ?? "");
}

function toInput(job: AdminJob, value: string): JobTimingInput {
  if (job.kind === "daily") {
    const [hour, minute] = value.split(":").map(Number);
    return { kind: "daily", hour, minute };
  }
  return { kind: "interval", interval_minutes: Number(value) };
}

function formatRun(iso: string | null, none: string): string {
  return iso ? new Date(iso).toLocaleString() : none;
}

interface TimingField {
  job: AdminJob;
  value: string;
  timezone: string;
  onChange: (value: string) => void;
}

function TimingInput({ field }: { field: TimingField }) {
  const { job, value, timezone, onChange } = field;
  if (job.kind === "daily") {
    return (
      <span className="flex items-center gap-2">
        <input type="time" aria-label="Time of day" value={value} onChange={(e) => onChange(e.target.value)} className={inputClass} />
        <span className={mutedClass}>{timezone}</span>
      </span>
    );
  }
  return (
    <span className="flex items-center gap-2">
      <input
        type="number"
        aria-label="Interval in minutes"
        min={job.min_minutes ?? 1}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`${inputClass} w-24`}
      />
      <span className={mutedClass}>minutes</span>
    </span>
  );
}

function ResetButton({ job, onReset }: { job: AdminJob; onReset: (value: string) => void }) {
  const reset = useResetJob(job.id);
  return (
    <>
      <button
        type="button"
        disabled={reset.isPending}
        onClick={() => reset.mutate(undefined, { onSuccess: () => onReset(initialValue({ ...job, timing: job.default })) })}
        className={outlineButtonClass}
      >
        Reset to default
      </button>
      <MutationError error={reset.error} fallback="Could not reset the schedule. Please try again." />
    </>
  );
}

export function JobRow({ job, timezone }: { job: AdminJob; timezone: string }) {
  const [value, setValue] = useState(() => initialValue(job));
  const save = useSaveJob(job.id);
  return (
    <div role="group" aria-label={job.label} className="space-y-2 py-3">
      <p className="text-sm font-medium text-ink">{job.label}</p>
      <div className="flex flex-wrap items-center gap-3">
        <TimingInput field={{ job, value, timezone, onChange: setValue }} />
        <button type="button" disabled={save.isPending} onClick={() => save.mutate(toInput(job, value))} className={primaryButtonClass}>
          Save
        </button>
        {!job.is_default && <ResetButton job={job} onReset={setValue} />}
      </div>
      <p className={mutedClass}>
        Next run: {formatRun(job.next_run, "not scheduled")}. Last run: {formatRun(job.last_run, "never")}.
      </p>
      <MutationError error={save.error} fallback="Could not save the schedule. Please try again." />
    </div>
  );
}
