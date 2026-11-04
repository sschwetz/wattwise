import { type ReactNode, useId } from "react";

/** One numbered step of the schedule editor, headed by an h3. */
export function Step({ number, title, children }: { number: number; title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="grid grid-cols-[1.75rem_1fr] gap-x-3 gap-y-3">
      <span aria-hidden="true" className="flex h-7 w-7 items-center justify-center rounded-full bg-surface-2 text-sm font-medium text-solar">
        {number}
      </span>
      <div className="min-w-0 space-y-3">
        <h3 id={id} className="pt-0.5 font-medium text-ink">
          {title}
        </h3>
        {children}
      </div>
    </section>
  );
}

/** A field's validation message. Give the field `aria-describedby={id}` and `aria-invalid`. */
export function FieldError({ id, message }: { id: string; message: string | undefined }) {
  if (!message) return null;
  return (
    <p id={id} role="alert" className="text-sm text-danger">
      {message}
    </p>
  );
}

