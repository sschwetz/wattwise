import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "../api/client";
import { buttonClass, noticeClass, secondaryButtonClass } from "./styles";

export interface DeleteControls {
  onConfirm: () => void;
  isPending: boolean;
  error: Error | null;
}

function ConfirmDelete({ onConfirm, onCancel }: { onConfirm: () => void; onCancel: () => void }) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);
  return (
    <div role="alertdialog" aria-labelledby="delete-schedule-title" aria-describedby="delete-schedule-body" className="rounded-md border border-line bg-surface-2 p-4">
      <h3 id="delete-schedule-title" className="font-medium text-ink">Delete this schedule?</h3>
      <p id="delete-schedule-body" className="mt-1 text-sm text-ink-muted">It stops running and its run history is removed.</p>
      <div className="mt-3 flex gap-2">
        <button type="button" ref={cancelRef} onClick={onCancel} className={secondaryButtonClass}>Cancel</button>
        <button type="button" onClick={onConfirm} className={`${buttonClass} bg-danger text-white`}>Delete</button>
      </div>
    </div>
  );
}

/** "Delete schedule" with an inline confirmation. The server refuses (409) while the unit is held off. */
export function DeleteSchedule({ controls }: { controls: DeleteControls }) {
  const [confirming, setConfirming] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => {
    setConfirming(false);
    buttonRef.current?.focus();
  }, []);
  return (
    <div className="space-y-3">
      <button type="button" ref={buttonRef} disabled={controls.isPending} onClick={() => setConfirming(true)} className={`${buttonClass} border border-danger/40 text-danger`}>
        Delete schedule
      </button>
      {controls.error && <p role="alert" className={noticeClass}>{errorMessage(controls.error, "Could not delete the schedule. Please try again.")}</p>}
      {confirming && (
        <ConfirmDelete
          onCancel={close}
          onConfirm={() => {
            close();
            controls.onConfirm();
          }}
        />
      )}
    </div>
  );
}
