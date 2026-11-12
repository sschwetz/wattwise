import { QueryError } from "../components/QueryError";
import { Spinner } from "../components/Spinner";
import { formatRunTime, outcomeLabel, outcomeTone, runDescription, stepLines, actionLabeler, actionsSummary } from "./runFormat";
import { useNow } from "../dashboard/useNow";
import { cardClass } from "./styles";
import type { ActorInstance, ActorRun, ActorSchedule } from "./types";
import { useScheduleRuns } from "./useActors";

/** The server's default and the number the history asks for. */
export const RUN_LIMIT = 50;

/** `summary` names every action of the schedule; `of` names one action by id. */
export interface ActionLabels {
  summary: string;
  of: (id: string) => string;
}

export interface RunHistoryProps {
  runs: ActorRun[];
  timezone: string;
  labels: ActionLabels;
}

interface RunRowData {
  run: ActorRun;
  timezone: string;
  labels: ActionLabels;
  now: Date;
}

function StepList({ run, labelFor }: { run: ActorRun; labelFor: (id: string) => string }) {
  const lines = stepLines(run, labelFor);
  if (lines.length === 0) return null;
  return (
    <ul aria-label="Steps" className="mt-1 space-y-0.5 text-xs">
      {lines.map((line, i) => (
        <li key={i} className={line.tone === "failed" ? "text-danger" : "text-ink-muted"}>{line.text}</li>
      ))}
    </ul>
  );
}

function RunRow({ row }: { row: RunRowData }) {
  const { run, timezone, labels, now } = row;
  return (
    <li className="flex items-start gap-3 py-2 text-sm">
      <time dateTime={run.trigger_at} className="w-32 shrink-0 text-ink-muted">{formatRunTime(run.trigger_at, timezone, now)}</time>
      <div className="min-w-0 flex-1 break-words">
        <span className="text-ink">{runDescription(run, labels.summary)}</span>
        <StepList run={run} labelFor={labels.of} />
      </div>
      <span data-testid="outcome" className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${outcomeTone(run.outcome)}`}>
        {outcomeLabel(run.outcome)}
      </span>
    </li>
  );
}

export function RunHistory({ runs, timezone, labels }: RunHistoryProps) {
  const now = useNow(60_000);
  return (
    <section className={`${cardClass} space-y-3`}>
      <h2 className="text-lg font-medium text-ink">Run history</h2>
      {runs.length === 0 ? (
        <p className="text-sm text-ink-muted">No runs yet.</p>
      ) : (
        <ul aria-label="Runs" className="divide-y divide-line">
          {runs.map((run) => <RunRow key={run.id} row={{ run, timezone, labels, now }} />)}
        </ul>
      )}
      {runs.length >= RUN_LIMIT && <p className="text-xs text-ink-muted">Showing the {RUN_LIMIT} newest runs.</p>}
    </section>
  );
}

/** Loads a schedule's runs and shows them. */
export function ScheduleRunHistory({ site, schedule, instance }: {
  site: { id: number; timezone: string };
  schedule: ActorSchedule;
  instance: ActorInstance;
}) {
  const runs = useScheduleRuns(site.id, schedule.id);
  const of = actionLabeler(instance);
  if (runs.isError) return <section className={cardClass}><QueryError error={runs.error} fallback="Could not load the run history." onRetry={() => void runs.refetch()} /></section>;
  if (!runs.data) return <section className={cardClass}><Spinner /></section>;
  return <RunHistory runs={runs.data} timezone={site.timezone} labels={{ summary: actionsSummary(schedule, of), of }} />;
}
