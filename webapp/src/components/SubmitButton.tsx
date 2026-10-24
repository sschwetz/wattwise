import { type ReactNode, useEffect, useState } from "react";
import { Spinner } from "./Spinner";

const SAVED_VISIBLE_MS = 4000;

export interface SaveState {
  isPending: boolean;
  isSuccess: boolean;
  submittedAt: number;
}

/** A submit button for a form backed by `mutation`: a spinner while the
 * request is in flight, then a "Saved" note beside it. The label stays the same
 * so the button keeps its width and accessible name. `layoutClass` positions
 * the button and its note together, e.g. in a grid. */
export function SubmitButton({
  mutation,
  buttonClass,
  layoutClass = "",
  disabled = false,
  children,
}: {
  mutation: SaveState;
  buttonClass: string;
  layoutClass?: string;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <span className={`inline-flex items-center gap-3 ${layoutClass}`}>
      <button
        type="submit"
        disabled={mutation.isPending || disabled}
        aria-busy={mutation.isPending}
        className={`${buttonClass} inline-flex items-center gap-2`}
      >
        {mutation.isPending && <Spinner className="h-3.5 w-3.5" />}
        {children}
      </button>
      <SaveStatus mutation={mutation} />
    </span>
  );
}

/** Confirms a save: "Saving…" while the mutation runs, then "Saved" for a few
 * seconds. `submittedAt` changes on every new submit (react-query's
 * `mutation.submittedAt`), so a repeat save shows the confirmation again. */
export function SaveStatus({ mutation }: { mutation: SaveState }) {
  const { isPending, isSuccess, submittedAt } = mutation;
  const [expiredFor, setExpiredFor] = useState<number | null>(null);

  useEffect(() => {
    if (!isSuccess) {
      return;
    }
    const timer = setTimeout(() => setExpiredFor(submittedAt), SAVED_VISIBLE_MS);
    return () => clearTimeout(timer);
  }, [isSuccess, submittedAt]);

  const showSaved = isSuccess && expiredFor !== submittedAt;
  return (
    <span role="status" className="text-sm">
      {isPending && <span className="text-ink-muted">Saving…</span>}
      {!isPending && showSaved && <span className="text-flow">✓ Saved</span>}
    </span>
  );
}
