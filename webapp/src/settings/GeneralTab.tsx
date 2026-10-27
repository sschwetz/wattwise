import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMe } from "../account/useAccount";
import { errorMessage } from "../api/client";
import { type Site, useUpdateSite } from "../sites/useSites";

const inputClass =
  "w-full rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50";
const buttonClass =
  "rounded-md px-3 py-2 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50";

function ConfirmDisable({ onConfirm, onCancel }: { onConfirm: () => void; onCancel: () => void }) {
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
    <div
      role="alertdialog"
      aria-labelledby="disable-title"
      aria-describedby="disable-body"
      className="rounded-md border border-line bg-surface-2 p-4"
    >
      <h3 id="disable-title" className="font-medium text-ink">
        Turn off collection?
      </h3>
      <p id="disable-body" className="mt-1 text-sm text-ink-muted">
        Collection will stop for this site. Existing data stays and you can turn collection back on later.
      </p>
      <div className="mt-3 flex gap-2">
        <button type="button" ref={cancelRef} onClick={onCancel} className={`${buttonClass} border border-line text-ink`}>
          Cancel
        </button>
        <button type="button" onClick={onConfirm} className={`${buttonClass} bg-danger text-white`}>
          Turn off
        </button>
      </div>
    </div>
  );
}

function EnabledToggle({ site, disabled }: { site: Site; disabled: boolean }) {
  const [confirming, setConfirming] = useState(false);
  const switchRef = useRef<HTMLButtonElement>(null);
  const update = useUpdateSite(site.id);

  const closeConfirm = useCallback(() => {
    setConfirming(false);
    switchRef.current?.focus();
  }, []);

  function handleClick() {
    if (site.enabled) setConfirming(true);
    else update.mutate({ enabled: true });
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <button
          type="button"
          ref={switchRef}
          role="switch"
          aria-checked={site.enabled}
          aria-label="Enabled"
          disabled={disabled || update.isPending}
          onClick={handleClick}
          className={`relative h-6 w-11 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50 ${
            site.enabled ? "bg-solar" : "bg-line"
          }`}
        >
          <span
            className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${
              site.enabled ? "translate-x-5" : ""
            }`}
          />
        </button>
        <span className="text-sm text-ink">Enabled</span>
        <span className="text-sm text-ink-muted">{site.enabled ? "Collecting" : "Collection is off"}</span>
      </div>
      {update.isError && (
        <p role="alert" data-testid="toggle-error" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
          {errorMessage(update.error, "Failed to change collection. Please try again.")}
        </p>
      )}
      {confirming && (
        <ConfirmDisable
          onCancel={closeConfirm}
          onConfirm={() => {
            closeConfirm();
            update.mutate({ enabled: false });
          }}
        />
      )}
    </div>
  );
}

function accessNote(meFailed: boolean, viewer: boolean): string | null {
  if (meFailed) return "Your account details could not be loaded, so settings are read-only.";
  if (viewer) return "Only the owner can change this site's settings.";
  return null;
}

export function GeneralTab({ site }: { site: Site }) {
  const me = useMe();
  const update = useUpdateSite(site.id);
  const [name, setName] = useState(site.name);
  const [timezone, setTimezone] = useState(site.timezone);
  const readOnly = me.data?.id !== site.owner_user_id;
  const note = accessNote(me.isError, me.isSuccess && readOnly);

  function handleSave(event: FormEvent) {
    event.preventDefault();
    update.mutate({ name, timezone });
  }

  return (
    <div className="space-y-6">
      {note && <p className="text-sm text-ink-muted">{note}</p>}
      <form onSubmit={handleSave} className="space-y-4">
        <label className="block space-y-1 text-sm text-ink">
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={readOnly} required className={inputClass} />
        </label>
        <label className="block space-y-1 text-sm text-ink">
          Time zone
          <input
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            placeholder="Timezone (e.g. Australia/Sydney)"
            autoComplete="off"
            spellCheck={false}
            disabled={readOnly}
            required
            className={inputClass}
          />
        </label>
        {update.isError && (
          <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
            {errorMessage(update.error, "Failed to save. Please try again.")}
          </p>
        )}
        {update.isSuccess && <p role="status" className="text-sm text-ink-muted">Saved</p>}
        <button type="submit" disabled={readOnly || update.isPending} className={`${buttonClass} bg-solar text-[#1a1203]`}>
          Save
        </button>
      </form>
      <EnabledToggle site={site} disabled={readOnly} />
      <Link to={`/sites/${site.id}`} className="inline-block text-sm text-solar underline">
        Open dashboard
      </Link>
    </div>
  );
}
