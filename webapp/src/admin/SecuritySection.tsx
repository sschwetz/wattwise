import { type FormEvent, useState } from "react";
import { ConfirmAction } from "./ConfirmAction";
import { MutationError, headingClass, inputClass, mutedClass, outlineButtonClass, primaryButtonClass, sectionClass } from "./adminUi";
import {
  useClearUserAmberKey,
  useResetUserTotp,
  useSendPasswordReset,
  useSetUserAmberKey,
} from "./useAdminUserMutations";
import type { AdminUser } from "./useAdminUsers";

function PasswordReset({ userId }: { userId: number }) {
  const reset = useSendPasswordReset(userId);
  return (
    <div className="space-y-2">
      <button type="button" disabled={reset.isPending} onClick={() => reset.mutate()} className={outlineButtonClass}>
        {reset.isSuccess ? "Reset email requested" : "Send password reset email"}
      </button>
      {reset.isSuccess && (
        <p className={mutedClass}>The request was accepted. Delivery is not confirmed.</p>
      )}
      <MutationError error={reset.error} fallback="Could not request the reset email. Please try again." />
    </div>
  );
}

function TotpReset({ userId }: { userId: number }) {
  const reset = useResetUserTotp(userId);
  return (
    <div className="space-y-2">
      <ConfirmAction
        label="Reset two-factor sign-in"
        title="Reset two-factor sign-in?"
        body="The user's authenticator and recovery codes are removed. They set up two-factor sign-in again at their next login."
        confirmLabel="Reset two-factor"
        disabled={reset.isPending}
        onConfirm={() => reset.mutate()}
      />
      {reset.isSuccess && <p className={mutedClass}>Two-factor sign-in was reset.</p>}
      <MutationError error={reset.error} fallback="Could not reset two-factor sign-in. Please try again." />
    </div>
  );
}

function AmberKeyForm({ userId, onDone }: { userId: number; onDone: () => void }) {
  const [value, setValue] = useState("");
  const setKey = useSetUserAmberKey(userId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setKey.mutate(value, { onSuccess: onDone });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2">
      <input
        type="password"
        aria-label="New Amber API key"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        autoComplete="off"
        spellCheck={false}
        required
        className={inputClass}
      />
      <MutationError error={setKey.error} fallback="Could not save the API key. Please try again." />
      <div className="flex gap-2">
        <button type="submit" disabled={setKey.isPending} className={primaryButtonClass}>
          Save key
        </button>
        <button type="button" onClick={onDone} className={outlineButtonClass}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function AmberKey({ user }: { user: AdminUser }) {
  const [editing, setEditing] = useState(false);
  const clear = useClearUserAmberKey(user.id);
  return (
    <div className="space-y-2">
      <p className="text-sm text-ink">Amber API key: {user.has_amber_api_key ? "set" : "not set"}</p>
      {editing ? (
        <AmberKeyForm userId={user.id} onDone={() => setEditing(false)} />
      ) : (
        <div className="flex gap-2">
          <button type="button" onClick={() => setEditing(true)} className={outlineButtonClass}>
            Change
          </button>
          {user.has_amber_api_key && (
            <ConfirmAction
              label="Clear"
              title="Clear the Amber API key?"
              body="Price data stops loading for this user's sites until a new key is set."
              confirmLabel="Clear key"
              disabled={clear.isPending}
              onConfirm={() => clear.mutate()}
            />
          )}
        </div>
      )}
      <MutationError error={clear.error} fallback="Could not clear the API key. Please try again." />
    </div>
  );
}

export function SecuritySection({ user }: { user: AdminUser }) {
  return (
    <section className={sectionClass}>
      <h2 className={headingClass}>Security</h2>
      <PasswordReset userId={user.id} />
      <TotpReset userId={user.id} />
      <AmberKey user={user} />
    </section>
  );
}
