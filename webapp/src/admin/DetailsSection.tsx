import { type FormEvent, useId, useState } from "react";
import { ApiError } from "../api/client";
import { SubmitButton } from "../components/SubmitButton";
import { Avatar } from "../layout/Avatar";
import { MutationError, headingClass, inputClass, outlineButtonClass, primaryButtonClass, sectionClass } from "./adminUi";
import { type AdminUserPatch, useRemoveUserAvatar, useUpdateAdminUser } from "./useAdminUserMutations";
import type { AdminUser } from "./useAdminUsers";

/** Only the fields that differ from the saved user, so an untouched field is never sent. */
function changedFields(user: AdminUser, displayName: string, email: string): AdminUserPatch {
  const patch: AdminUserPatch = {};
  const name = displayName.trim();
  if (name !== (user.display_name ?? "")) patch.display_name = name === "" ? null : name;
  if (email.trim() !== user.email) patch.email = email.trim();
  return patch;
}

function AvatarRow({ user }: { user: AdminUser }) {
  const removeAvatar = useRemoveUserAvatar(user.id);
  return (
    <div className="flex items-center gap-4">
      <Avatar name={user.display_name} email={user.email} imageUrl={null} />
      {user.has_avatar && (
        <button type="button" disabled={removeAvatar.isPending} onClick={() => removeAvatar.mutate()} className={outlineButtonClass}>
          Remove photo
        </button>
      )}
      <MutationError error={removeAvatar.error} fallback="Could not remove the photo. Please try again." />
    </div>
  );
}

function DetailsForm({ user }: { user: AdminUser }) {
  const [displayName, setDisplayName] = useState(user.display_name ?? "");
  const [email, setEmail] = useState(user.email);
  const update = useUpdateAdminUser(user.id);
  const emailErrorId = useId();
  const patch = changedFields(user, displayName, email);
  const emailTaken = update.error instanceof ApiError && update.error.code === "email_taken";

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    update.mutate(patch);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <label className="flex flex-col text-sm text-ink-muted">
        Display name
        <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} spellCheck={false} className={inputClass} />
      </label>
      <label className="flex flex-col text-sm text-ink-muted">
        Email
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-invalid={emailTaken}
          aria-describedby={emailTaken ? emailErrorId : undefined}
          spellCheck={false}
          className={inputClass}
        />
        {emailTaken && (
          <span id={emailErrorId} role="alert" className="mt-1 text-danger">
            {update.error?.message}
          </span>
        )}
      </label>
      {!emailTaken && <MutationError error={update.error} fallback="Could not save the changes. Please try again." />}
      <SubmitButton mutation={update} buttonClass={primaryButtonClass} disabled={Object.keys(patch).length === 0}>
        Save
      </SubmitButton>
    </form>
  );
}

export function DetailsSection({ user }: { user: AdminUser }) {
  return (
    <section className={sectionClass}>
      <h2 className={headingClass}>Details</h2>
      <AvatarRow user={user} />
      <DetailsForm key={`${user.display_name}|${user.email}`} user={user} />
    </section>
  );
}
