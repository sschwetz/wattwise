import type { ReactNode } from "react";

/** Disables every form control inside it when `readOnly` is set. */
export function ReadOnlyFieldset({ readOnly, children }: { readOnly: boolean; children: ReactNode }) {
  return (
    <fieldset disabled={readOnly} className="min-w-0 space-y-6">
      {children}
    </fieldset>
  );
}
