import { type FormEvent, useState } from "react";
import { apiFetch } from "../api/client";
import { useSubmitGuard } from "../hooks/useSubmitGuard";
import { AuthCard } from "../layout/AuthCard";

interface RequestResponse {
  message: string;
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { isSubmitting, guard } = useSubmitGuard();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    await guard(async () => {
      try {
        const result = await apiFetch<RequestResponse>("/api/v1/auth/password-reset/request", {
          method: "POST",
          body: JSON.stringify({ email }),
        });
        setMessage(result.message);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not send a reset link. Please try again.");
      }
    });
  }

  if (message) {
    return (
      <AuthCard title="Forgot your password?">
        <p className="text-ink-muted">{message}</p>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Forgot your password?">
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email"
          aria-label="Email"
          name="email"
          autoComplete="email"
          spellCheck={false}
          className="w-full rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
          required
        />
        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full rounded-md bg-solar px-3 py-2 font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50"
        >
          Send reset link
        </button>
      </form>
    </AuthCard>
  );
}
