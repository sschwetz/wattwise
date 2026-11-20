import { useCallback, useEffect, useId, useRef, useState } from "react";
import { buttonClass, secondaryButtonClass } from "../actors/styles";

interface PromptProps {
  title: string;
  body: string;
  confirmLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
}

interface ConfirmActionProps {
  label: string;
  /** Names the button when several share the same visible label. */
  ariaLabel?: string;
  title: string;
  body: string;
  confirmLabel: string;
  disabled?: boolean;
  onConfirm: () => void;
}

function ConfirmPrompt({ title, body, confirmLabel, onCancel, onConfirm }: PromptProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const bodyId = useId();
  useEffect(() => {
    cancelRef.current?.focus();
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);
  return (
    <div role="alertdialog" aria-label={title} aria-describedby={bodyId} className="rounded-md border border-line bg-surface-2 p-4">
      <h3 className="font-medium text-ink">{title}</h3>
      <p id={bodyId} className="mt-1 text-sm text-ink-muted">{body}</p>
      <div className="mt-3 flex gap-2">
        <button type="button" ref={cancelRef} onClick={onCancel} className={secondaryButtonClass}>Cancel</button>
        <button type="button" onClick={onConfirm} className={`${buttonClass} bg-danger text-white`}>{confirmLabel}</button>
      </div>
    </div>
  );
}

/** A destructive button that asks first, in the inline style of the schedule's
 * delete confirmation. Cancel and Escape close the prompt and do nothing. */
export function ConfirmAction({ label, ariaLabel, title, body, confirmLabel, disabled = false, onConfirm }: ConfirmActionProps) {
  const [confirming, setConfirming] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => {
    setConfirming(false);
    buttonRef.current?.focus();
  }, []);
  function confirm() {
    close();
    onConfirm();
  }
  return (
    <div className="space-y-3">
      <button
        type="button"
        ref={buttonRef}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => setConfirming(true)}
        className={`${buttonClass} border border-danger/40 text-danger`}
      >
        {label}
      </button>
      {confirming && <ConfirmPrompt title={title} body={body} confirmLabel={confirmLabel} onCancel={close} onConfirm={confirm} />}
    </div>
  );
}
