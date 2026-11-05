// Unsaved schedule edits: the editor reports whether its form differs from
// the saved schedule, and the Schedule page asks before a selection change or
// a navigation would throw those edits away.
import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useLeaveGuard } from "../hooks/useLeaveGuard";
import type { Selection } from "./ScheduleList";
import { UnsavedChangesDialog } from "./UnsavedChangesDialog";

/** Tells the page whether the editor holds unsaved edits. Outside a page it does nothing. */
export const UnsavedEditsContext = createContext<(dirty: boolean) => void>(() => {});

/** Report `dirty` after every render and clean on unmount. Returns the reporter, so a
 * caller can mark the form clean at once, before a selection change it starts itself. */
export function useReportDirty(dirty: boolean): (dirty: boolean) => void {
  const report = useContext(UnsavedEditsContext);
  useEffect(() => report(dirty));
  useEffect(() => () => report(false), [report]);
  return report;
}

function sameSelection(a: Selection, b: Selection): boolean {
  if (a === null || b === null) return a === b;
  if (a.kind === "new" || b.kind === "new") return a.kind === b.kind;
  return a.id === b.id;
}

export interface GuardedSelection {
  /** Applies `next` at once, or holds it behind the dialog while there are unsaved edits. */
  select: (next: Selection) => void;
  /** Wraps the editor so it can report its state. */
  provide: (children: ReactNode) => ReactNode;
  /** The dialog while a selection change or a navigation is on hold, else null. */
  dialog: ReactNode;
}

function useDirtyFlag() {
  const dirtyRef = useRef(false);
  const [dirty, setDirty] = useState(false);
  const report = useCallback((next: boolean) => {
    dirtyRef.current = next;
    setDirty(next);
  }, []);
  return { dirty, dirtyRef, report };
}

/** Selection changes that ask first while the editor holds unsaved edits. `apply` sets the selection. */
export function useGuardedSelection(selected: Selection, apply: (next: Selection) => void): GuardedSelection {
  const { dirty, dirtyRef, report } = useDirtyFlag();
  const [held, setHeld] = useState<{ next: Selection } | null>(null);
  const leave = useLeaveGuard(dirty);
  // The edits went away underneath the dialog (the editor unmounted), so there is nothing to ask about.
  if (held !== null && !dirty) setHeld(null);
  const select = (next: Selection) => {
    if (dirtyRef.current && !sameSelection(next, selected)) setHeld({ next });
    else apply(next);
  };
  const keep = () => {
    setHeld(null);
    leave.reset();
  };
  const discard = () => {
    report(false);
    setHeld(null);
    if (leave.blocked) leave.proceed();
    else if (held) apply(held.next);
  };
  const open = held !== null || leave.blocked;
  return {
    select,
    provide: (children) => <UnsavedEditsContext.Provider value={report}>{children}</UnsavedEditsContext.Provider>,
    dialog: open ? <UnsavedChangesDialog onKeep={keep} onDiscard={discard} /> : null,
  };
}
