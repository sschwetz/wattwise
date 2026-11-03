import { errorMessage } from "../api/client";

/** Shown in place of a section's content when its first load fails, so an API error is not mistaken for "nothing here yet". */
export function QueryError({ error, fallback, onRetry }: { error: unknown; fallback: string; onRetry: () => void }) {
  return (
    <div className="flex flex-col items-start gap-2 text-sm text-ink">
      <p role="alert" className="text-danger">
        {errorMessage(error, fallback)}
      </p>
      <button type="button" onClick={onRetry} className="rounded-md border border-line px-3 py-1 hover:bg-surface-2">
        Retry
      </button>
    </div>
  );
}
