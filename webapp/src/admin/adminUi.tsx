import { errorMessage } from "../api/client";

export const sectionClass = "space-y-4 rounded-xl border border-line bg-surface p-5";
export const headingClass = "font-display text-lg font-semibold text-ink";
export const errorClass = "rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger";
export const mutedClass = "text-sm text-ink-muted";
export const selectClass =
  "rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50";
export const outlineButtonClass =
  "rounded-md border border-line px-3 py-2 text-sm text-ink hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50";
export const primaryButtonClass =
  "rounded-md bg-solar px-3 py-2 text-sm font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50";

/** The message of a failed mutation, or nothing when there is no error. */
export function MutationError({ error, fallback }: { error: unknown; fallback: string }) {
  if (!error) return null;
  return (
    <p role="alert" className={errorClass}>
      {errorMessage(error, fallback)}
    </p>
  );
}
