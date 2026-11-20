import { MutationError, mutedClass, outlineButtonClass } from "./adminUi";
import { useIsSavingSettings, useSendTestEmail } from "./useAdminMail";

export function TestEmailRow({ hasActive }: { hasActive: boolean }) {
  const test = useSendTestEmail();
  const saving = useIsSavingSettings();
  return (
    <div className="space-y-2">
      <button
        type="button"
        disabled={!hasActive || test.isPending || saving}
        onClick={() => test.mutate()}
        className={outlineButtonClass}
      >
        Send test email
      </button>
      {test.isSuccess && (
        <p role="status" className={mutedClass}>Test email queued. Check the inbox; delivery depends on the provider.</p>
      )}
      <MutationError error={test.error} fallback="Could not queue the test email. Please try again." />
    </div>
  );
}
