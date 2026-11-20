import { ApiError } from "../api/client";
import { ConfirmAction } from "./ConfirmAction";
import { MutationError, headingClass, mutedClass, outlineButtonClass, sectionClass } from "./adminUi";
import { useUpdateAdminUser } from "./useAdminUserMutations";
import type { AdminUser } from "./useAdminUsers";

interface AdministratorSectionProps {
  user: AdminUser;
  allUsers: AdminUser[];
}

export function AdministratorSection({ user, allUsers }: AdministratorSectionProps) {
  const update = useUpdateAdminUser(user.id);
  const onlyAdmin = user.is_admin && allUsers.filter((u) => u.is_admin).length === 1;
  const refused = update.error instanceof ApiError && update.error.code === "last_admin";
  const locked = onlyAdmin || refused;

  return (
    <section className={sectionClass}>
      <h2 className={headingClass}>Administrator</h2>
      <p className={mutedClass}>
        {user.is_admin ? "This user is an administrator." : "This user is not an administrator."}
      </p>
      {user.is_admin ? (
        <ConfirmAction
          label="Remove administrator"
          title="Remove administrator access?"
          body="This user loses access to the admin pages."
          confirmLabel="Remove admin"
          disabled={locked || update.isPending}
          onConfirm={() => update.mutate({ is_admin: false })}
        />
      ) : (
        <button type="button" disabled={update.isPending} onClick={() => update.mutate({ is_admin: true })} className={outlineButtonClass}>
          Make administrator
        </button>
      )}
      {onlyAdmin && <p className={mutedClass}>This is the only administrator, so the flag cannot be removed.</p>}
      <MutationError error={update.error} fallback="Could not change the administrator flag. Please try again." />
    </section>
  );
}
