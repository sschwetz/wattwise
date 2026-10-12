import { useCallback, useState } from "react";

/** Tracks in-flight-ness for a raw `apiFetch` submit handler so the button can
 * disable itself for the duration, guarding against double-submit. Pages that
 * already get this from a react-query mutation's `isPending` don't need it. */
export function useSubmitGuard() {
  const [isSubmitting, setIsSubmitting] = useState(false);

  const guard = useCallback(async (action: () => Promise<void>) => {
    setIsSubmitting(true);
    try {
      await action();
    } finally {
      setIsSubmitting(false);
    }
  }, []);

  return { isSubmitting, guard };
}
