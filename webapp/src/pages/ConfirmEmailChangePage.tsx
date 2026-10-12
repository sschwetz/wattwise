import { type FormEvent, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useConfirmEmailChange } from "../account/useAccount";
import { AuthCard } from "../layout/AuthCard";

export function ConfirmEmailChangePage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const navigate = useNavigate();
  const confirmEmailChange = useConfirmEmailChange();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await confirmEmailChange.mutateAsync(token);
      setConfirmed(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "This confirmation link is invalid or has expired");
    }
  }

  if (confirmed) {
    return (
      <AuthCard title="Email confirmed">
        <p className="text-ink-muted">Your email address has been updated.</p>
        <button
          onClick={() => navigate("/login")}
          className="mt-4 w-full rounded-md bg-solar px-3 py-2 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
        >
          Go to login
        </button>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Confirm your new email address">
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && (
          <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={confirmEmailChange.isPending}
          className="w-full rounded-md bg-solar px-3 py-2 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
        >
          Confirm email change
        </button>
      </form>
    </AuthCard>
  );
}
