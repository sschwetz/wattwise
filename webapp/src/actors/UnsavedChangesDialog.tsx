import { type KeyboardEvent, type RefObject, useEffect, useId, useRef } from "react";
import { buttonClass, secondaryButtonClass } from "./styles";

export interface UnsavedChangesDialogProps {
  /** Close the dialog and leave the edits as they are. Also Escape and a click on the backdrop. */
  onKeep: () => void;
  /** Throw the edits away and carry on with what the owner asked for. */
  onDiscard: () => void;
}

/** Focus Keep editing while open, and give focus back to the control that had it when the dialog closes. */
function useDialogFocus(first: RefObject<HTMLButtonElement | null>) {
  useEffect(() => {
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    first.current?.focus();
    return () => {
      if (before?.isConnected) before.focus();
    };
  }, [first]);
}

function useEscape(onKeep: () => void) {
  useEffect(() => {
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") onKeep();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onKeep]);
}

/** Tab and Shift+Tab move between the two buttons only. */
function trapTab(event: KeyboardEvent<HTMLDivElement>, buttons: (HTMLButtonElement | null)[]) {
  if (event.key !== "Tab") return;
  event.preventDefault();
  const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
  const next = (at + (event.shiftKey ? buttons.length - 1 : 1)) % buttons.length;
  buttons[at === -1 ? 0 : next]?.focus();
}

/** Asks before unsaved schedule edits are thrown away. */
export function UnsavedChangesDialog({ onKeep, onDiscard }: UnsavedChangesDialogProps) {
  const id = useId();
  const keepRef = useRef<HTMLButtonElement>(null);
  const discardRef = useRef<HTMLButtonElement>(null);
  useDialogFocus(keepRef);
  useEscape(onKeep);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div data-testid="unsaved-changes-backdrop" className="absolute inset-0 bg-black/40" onClick={onKeep} />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-body`}
        onKeyDown={(event) => trapTab(event, [keepRef.current, discardRef.current])}
        className="relative w-full max-w-sm rounded-md border border-line bg-surface p-4 shadow-lg"
      >
        <h2 id={`${id}-title`} className="font-medium text-ink">Discard unsaved changes?</h2>
        <p id={`${id}-body`} className="mt-1 text-sm text-ink-muted">You have unsaved changes to this schedule. If you leave now, they are lost.</p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" ref={keepRef} onClick={onKeep} className={secondaryButtonClass}>Keep editing</button>
          <button type="button" ref={discardRef} onClick={onDiscard} className={`${buttonClass} bg-danger text-white`}>Discard changes</button>
        </div>
      </div>
    </div>
  );
}
